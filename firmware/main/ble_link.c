#include "ble_link.h"

#include <stdatomic.h>
#include <string.h>

#include "app.h"
#include "console.h"
#include "esp_check.h"
#include "esp_log.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#define LINK_UUID(n) BLE_UUID128_INIT(0x64, 0x65, 0x4d, 0x6e, 0x75, 0x46, 0x4b, 0x9a, \
                                      0x8e, 0x4a, 0x0e, 0x6b, n, 0x10, 0x2f, 0x6d)
#define STATE_VALUE_MAX 16

static const char *TAG = "ble";
static const char *DEVICE_NAME = "FunMedal";
static const ble_uuid128_t service_uuid = LINK_UUID(0x01);
static const ble_uuid128_t state_uuid = LINK_UUID(0x02);

static uint8_t own_addr_type;
static _Atomic uint32_t conn_handle = BLE_HS_CONN_HANDLE_NONE;
static _Atomic uint32_t state_writes;

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

static const struct ble_gatt_svc_def services[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &service_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &state_uuid.u,
                .access_cb = state_access,
                .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE,
            },
            {0},
        },
    },
    {0},
};

static int gap_event(struct ble_gap_event *event, void *arg)
{
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
    case BLE_GAP_EVENT_DISCONNECT:
        atomic_store(&conn_handle, BLE_HS_CONN_HANDLE_NONE);
        console_event("BLE disconnected reason=0x%x\n", event->disconnect.reason);
        advertise();
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

esp_err_t ble_link_start(void)
{
    ESP_RETURN_ON_ERROR(nimble_port_init(), TAG, "nimble init");
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ESP_RETURN_ON_FALSE(ble_gatts_count_cfg(services) == 0, ESP_FAIL, TAG, "gatt count");
    ESP_RETURN_ON_FALSE(ble_gatts_add_svcs(services) == 0, ESP_FAIL, TAG, "gatt add");
    ESP_RETURN_ON_FALSE(ble_svc_gap_device_name_set(DEVICE_NAME) == 0, ESP_FAIL, TAG, "device name");
    nimble_port_freertos_init(host_task);
    return ESP_OK;
}
