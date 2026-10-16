#include "ble_link.h"

#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "app.h"
#include "console.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_random.h"
#include "host/ble_hs.h"
#include "host/ble_store.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#define LINK_UUID(n) BLE_UUID128_INIT(0x64, 0x65, 0x4d, 0x6e, 0x75, 0x46, 0x4b, 0x9a, \
                                      0x8e, 0x4a, 0x0e, 0x6b, n, 0x10, 0x2f, 0x6d)
#define STATE_VALUE_MAX 16
#define DECISION_VALUE_MAX 24
#define PASSKEY_RANGE 1000000
#define SECURE_FLAGS_READ (BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_READ_ENC | BLE_GATT_CHR_F_READ_AUTHEN)
#define SECURE_FLAGS_WRITE (BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_ENC | BLE_GATT_CHR_F_WRITE_AUTHEN)
#define SECURE_FLAGS_NOTIFY \
    (BLE_GATT_CHR_F_NOTIFY | BLE_GATT_CHR_F_NOTIFY_INDICATE_ENC | BLE_GATT_CHR_F_NOTIFY_INDICATE_AUTHEN)

void ble_store_config_init(void);

static const char *TAG = "ble";
static const char *DEVICE_NAME = "FunMedal";
static const ble_uuid128_t service_uuid = LINK_UUID(0x01);
static const ble_uuid128_t state_uuid = LINK_UUID(0x02);
static const ble_uuid128_t prompt_uuid = LINK_UUID(0x03);
static const ble_uuid128_t decision_uuid = LINK_UUID(0x04);

static uint8_t own_addr_type;
static uint16_t decision_handle;
static _Atomic uint32_t conn_handle = BLE_HS_CONN_HANDLE_NONE;
static _Atomic uint32_t state_writes;
static _Atomic bool decision_subscribed;
static _Atomic bool pairing;
static _Atomic uint32_t numcmp_conn = BLE_HS_CONN_HANDLE_NONE;

static void advertise(void);

static int state_write(struct ble_gatt_access_ctxt *ctxt)
{
    char value[STATE_VALUE_MAX];
    uint16_t len = 0;
    if (ble_hs_mbuf_to_flat(ctxt->om, value, sizeof(value), &len) != 0) {
        console_event("BLE reject length\n");
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    pet_state_t state;
    if (!scene_state_from_bytes(value, len, &state)) {
        console_event("BLE reject name\n");
        return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    }
    atomic_fetch_add(&state_writes, 1);
    console_event("BLE state %s\n", scene_state_name(state));
    app_set_state(state);
    return 0;
}

static int state_read(struct ble_gatt_access_ctxt *ctxt)
{
    scene_t scene;
    app_get_scene(&scene);
    const char *name = scene_state_name(scene.state);
    return os_mbuf_append(ctxt->om, name, (uint16_t)strlen(name)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static int state_access(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return state_write(ctxt);
    }
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        return state_read(ctxt);
    }
    return BLE_ATT_ERR_UNLIKELY;
}

static int prompt_access(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    static char value[PROMPT_VALUE_MAX];
    static prompt_t prompt;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    uint16_t len = 0;
    if (ble_hs_mbuf_to_flat(ctxt->om, value, sizeof(value), &len) != 0) {
        console_event("BLE reject prompt length\n");
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    if (!prompt_parse(value, len, &prompt)) {
        console_event("BLE reject prompt format\n");
        return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    }
    app_set_prompt(&prompt);
    return 0;
}

static int decision_access(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    return BLE_ATT_ERR_UNLIKELY;
}

static const struct ble_gatt_svc_def services[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &service_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &state_uuid.u,
                .access_cb = state_access,
                .flags = SECURE_FLAGS_READ | SECURE_FLAGS_WRITE,
            },
            {
                .uuid = &prompt_uuid.u,
                .access_cb = prompt_access,
                .flags = SECURE_FLAGS_WRITE,
            },
            {
                .uuid = &decision_uuid.u,
                .access_cb = decision_access,
                .flags = SECURE_FLAGS_NOTIFY,
                .val_handle = &decision_handle,
            },
            {0},
        },
    },
    {0},
};

static bool security_of(uint16_t conn, struct ble_gap_conn_desc *desc)
{
    return conn != BLE_HS_CONN_HANDLE_NONE && ble_gap_conn_find(conn, desc) == 0;
}

static bool link_secure(uint16_t conn)
{
    struct ble_gap_conn_desc desc;
    return security_of(conn, &desc) && desc.sec_state.encrypted && desc.sec_state.authenticated &&
           desc.sec_state.bonded;
}

static void report_security(uint16_t conn)
{
    struct ble_gap_conn_desc desc;
    if (security_of(conn, &desc)) {
        console_event("BLE sec encrypted=%d authenticated=%d bonded=%d key_size=%d\n", desc.sec_state.encrypted,
                      desc.sec_state.authenticated, desc.sec_state.bonded, desc.sec_state.key_size);
    }
}

static void end_pairing_screen(void)
{
    atomic_store(&numcmp_conn, BLE_HS_CONN_HANDLE_NONE);
    app_set_pairing(SCENE_PAIRING_NONE, 0);
}

static void passkey_action(const struct ble_gap_event *event)
{
    uint16_t conn = event->passkey.conn_handle;
    atomic_store(&pairing, true);
    if (event->passkey.params.action == BLE_SM_IOACT_NUMCMP) {
        uint32_t code = event->passkey.params.numcmp;
        atomic_store(&numcmp_conn, conn);
        app_set_pairing(SCENE_PAIRING_NUMCMP, code);
        console_event("PAIR numcmp %06lu\n", (unsigned long)code);
        return;
    }
    if (event->passkey.params.action == BLE_SM_IOACT_DISP) {
        struct ble_sm_io io = {.action = BLE_SM_IOACT_DISP, .passkey = esp_random() % PASSKEY_RANGE};
        app_set_pairing(SCENE_PAIRING_PASSKEY, io.passkey);
        console_event("PAIR passkey %06lu\n", (unsigned long)io.passkey);
        int rc = ble_sm_inject_io(conn, &io);
        if (rc != 0) {
            console_event("PAIR inject failed rc=%d\n", rc);
        }
        return;
    }
    console_event("PAIR unsupported action=%d\n", event->passkey.params.action);
}

static int gap_event(struct ble_gap_event *event, void *arg)
{
    struct ble_gap_conn_desc desc;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status != 0) {
            console_event("BLE connect failed status=%d\n", event->connect.status);
            advertise();
            return 0;
        }
        atomic_store(&conn_handle, event->connect.conn_handle);
        console_event("BLE connected\n");
        return 0;
    case BLE_GAP_EVENT_DISCONNECT: {
        atomic_store(&conn_handle, BLE_HS_CONN_HANDLE_NONE);
        atomic_store(&decision_subscribed, false);
        atomic_store(&pairing, false);
        end_pairing_screen();
        prompt_t none = {.active = false};
        app_set_prompt(&none);
        console_event("BLE disconnected reason=0x%x\n", event->disconnect.reason);
        advertise();
        return 0;
    }
    case BLE_GAP_EVENT_ENC_CHANGE:
        report_security(event->enc_change.conn_handle);
        if (atomic_exchange(&pairing, false)) {
            console_event("PAIR done status=%d\n", event->enc_change.status);
        }
        end_pairing_screen();
        return 0;
    case BLE_GAP_EVENT_PASSKEY_ACTION:
        passkey_action(event);
        return 0;
    case BLE_GAP_EVENT_REPEAT_PAIRING:
        if (ble_gap_conn_find(event->repeat_pairing.conn_handle, &desc) == 0) {
            ble_store_util_delete_peer(&desc.peer_id_addr);
        }
        console_event("PAIR repeat\n");
        return BLE_GAP_REPEAT_PAIRING_RETRY;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.attr_handle == decision_handle) {
            atomic_store(&decision_subscribed, event->subscribe.cur_notify != 0);
            console_event("BLE subscribe decision=%d\n", event->subscribe.cur_notify);
        }
        return 0;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        advertise();
        return 0;
    default:
        return 0;
    }
}

static void advertise(void)
{
    struct ble_hs_adv_fields fields = {0};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = &service_uuid;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;
    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv fields rc=%d", rc);
        return;
    }
    struct ble_hs_adv_fields rsp = {0};
    rsp.name = (const uint8_t *)DEVICE_NAME;
    rsp.name_len = strlen(DEVICE_NAME);
    rsp.name_is_complete = 1;
    rc = ble_gap_adv_rsp_set_fields(&rsp);
    if (rc != 0) {
        ESP_LOGE(TAG, "scan rsp rc=%d", rc);
        return;
    }
    struct ble_gap_adv_params params = {0};
    params.conn_mode = BLE_GAP_CONN_MODE_UND;
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;
    rc = ble_gap_adv_start(own_addr_type, NULL, BLE_HS_FOREVER, &params, gap_event, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv start rc=%d", rc);
    }
}

static void on_sync(void)
{
    ble_hs_util_ensure_addr(0);
    ble_hs_id_infer_auto(0, &own_addr_type);
    advertise();
}

static void on_reset(int reason)
{
    ESP_LOGE(TAG, "host reset reason=%d", reason);
}

static void host_task(void *param)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

bool ble_link_connected(void)
{
    return atomic_load(&conn_handle) != BLE_HS_CONN_HANDLE_NONE;
}

uint32_t ble_link_writes(void)
{
    return atomic_load(&state_writes);
}

bool ble_link_send_decision(uint32_t id, bool allow)
{
    uint16_t conn = (uint16_t)atomic_load(&conn_handle);
    if (!atomic_load(&decision_subscribed) || !link_secure(conn)) {
        return false;
    }
    char value[DECISION_VALUE_MAX];
    int len = snprintf(value, sizeof(value), "%lu %s", (unsigned long)id, allow ? "allow" : "deny");
    struct os_mbuf *om = ble_hs_mbuf_from_flat(value, (uint16_t)len);
    return om != NULL && ble_gatts_notify_custom(conn, decision_handle, om) == 0;
}

void ble_link_confirm_pairing(bool accept)
{
    uint16_t conn = (uint16_t)atomic_exchange(&numcmp_conn, BLE_HS_CONN_HANDLE_NONE);
    if (conn == BLE_HS_CONN_HANDLE_NONE) {
        return;
    }
    struct ble_sm_io io = {.action = BLE_SM_IOACT_NUMCMP, .numcmp_accept = accept};
    int rc = ble_sm_inject_io(conn, &io);
    console_event("PAIR confirm accept=%d rc=%d\n", accept, rc);
    app_set_pairing(SCENE_PAIRING_NONE, 0);
}

void ble_link_security(char *out, size_t size)
{
    struct ble_gap_conn_desc desc;
    uint16_t conn = (uint16_t)atomic_load(&conn_handle);
    if (!security_of(conn, &desc) || !desc.sec_state.encrypted) {
        snprintf(out, size, "none");
        return;
    }
    snprintf(out, size, "encrypted%s%s", desc.sec_state.authenticated ? ",authenticated" : "",
             desc.sec_state.bonded ? ",bonded" : "");
}

int ble_link_unpair(void)
{
    return ble_store_clear();
}

esp_err_t ble_link_start(void)
{
    ESP_RETURN_ON_ERROR(nimble_port_init(), TAG, "nimble init");
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;
    ble_hs_cfg.sm_io_cap = BLE_SM_IO_CAP_DISP_YES_NO;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 1;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ESP_RETURN_ON_FALSE(ble_gatts_count_cfg(services) == 0, ESP_FAIL, TAG, "gatt count");
    ESP_RETURN_ON_FALSE(ble_gatts_add_svcs(services) == 0, ESP_FAIL, TAG, "gatt add");
    ESP_RETURN_ON_FALSE(ble_svc_gap_device_name_set(DEVICE_NAME) == 0, ESP_FAIL, TAG, "device name");
    ble_store_config_init();
    nimble_port_freertos_init(host_task);
    return ESP_OK;
}
