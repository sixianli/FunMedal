#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#define BENCH_UUID(n) BLE_UUID128_INIT(0x64, 0x65, 0x4d, 0x6e, 0x75, 0x46, 0x4b, 0x9a, \
                                       0x8e, 0x4a, 0x0e, 0x6b, n, 0x00, 0x2f, 0x6d)

#define PIPELINE_DEPTH 16
#define MAX_PAYLOAD 514
#define PREFERRED_MTU 517
#define LL_OCTETS 251
#define LL_TIME_US 2120
#define SUPERVISION_TIMEOUT_10MS 400

static const char *TAG = "blebench";
static const char *DEVICE_NAME = "FunMedal-Bench";

static const ble_uuid128_t svc_uuid = BENCH_UUID(0x01);
static const ble_uuid128_t tx_uuid = BENCH_UUID(0x02);
static const ble_uuid128_t rx_uuid = BENCH_UUID(0x03);
static const ble_uuid128_t ctrl_uuid = BENCH_UUID(0x04);

static uint16_t tx_handle;
static volatile uint16_t conn_handle = BLE_HS_CONN_HANDLE_NONE;
static volatile bool tx_subscribed;
static volatile bool streaming;
static volatile uint32_t stream_seconds;
static uint8_t own_addr_type;
static SemaphoreHandle_t credit_sem;
static TaskHandle_t stream_task_handle;

static struct {
    uint32_t bytes;
    uint32_t packets;
    uint32_t seq_gaps;
    uint32_t next_seq;
    uint16_t first_len;
    int64_t first_us;
    int64_t last_us;
} rx_stats;

static struct {
    uint32_t bytes;
    uint32_t packets;
    uint32_t retries;
    uint16_t payload;
    int64_t start_us;
    int64_t end_us;
} tx_stats;

static struct {
    uint16_t tx_octets;
    uint16_t tx_time;
    uint16_t rx_octets;
    uint16_t rx_time;
} data_len;

static void advertise(void);

static int notify_bytes(const void *data, uint16_t len)
{
    struct os_mbuf *om = ble_hs_mbuf_from_flat(data, len);
    if (om == NULL) {
        return BLE_HS_ENOMEM;
    }
    return ble_gatts_notify_custom(conn_handle, tx_handle, om);
}

static void send_summary(void)
{
    char buf[200];
    buf[0] = 'F';
    int n = snprintf(buf + 1, sizeof(buf) - 1,
                     "{\"tx_bytes\":%lu,\"tx_packets\":%lu,\"tx_payload\":%u,\"tx_us\":%lld,\"tx_retries\":%lu}",
                     (unsigned long)tx_stats.bytes, (unsigned long)tx_stats.packets, tx_stats.payload,
                     (long long)(tx_stats.end_us - tx_stats.start_us), (unsigned long)tx_stats.retries);
    for (int attempt = 0; attempt < 100 && conn_handle != BLE_HS_CONN_HANDLE_NONE; attempt++) {
        if (notify_bytes(buf, (uint16_t)(n + 1)) == 0) {
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    ESP_LOGW(TAG, "summary not sent");
}

static void stream_task(void *arg)
{
    static uint8_t payload[MAX_PAYLOAD];
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        uint16_t mtu = ble_att_mtu(conn_handle);
        uint16_t len = mtu > 3 ? mtu - 3 : 20;
        if (len > MAX_PAYLOAD) {
            len = MAX_PAYLOAD;
        }
        memset(&tx_stats, 0, sizeof(tx_stats));
        tx_stats.payload = len;
        payload[0] = 'D';
        for (uint16_t i = 5; i < len; i++) {
            payload[i] = (uint8_t)i;
        }
        while (xSemaphoreTake(credit_sem, 0) == pdTRUE) {
        }
        for (int i = 0; i < PIPELINE_DEPTH; i++) {
            xSemaphoreGive(credit_sem);
        }
        streaming = true;
        uint32_t seq = 0;
        tx_stats.start_us = esp_timer_get_time();
        int64_t deadline = tx_stats.start_us + (int64_t)stream_seconds * 1000000;
        while (esp_timer_get_time() < deadline && conn_handle != BLE_HS_CONN_HANDLE_NONE && tx_subscribed) {
            if (xSemaphoreTake(credit_sem, pdMS_TO_TICKS(100)) != pdTRUE) {
                continue;
            }
            memcpy(&payload[1], &seq, sizeof(seq));
            if (notify_bytes(payload, len) != 0) {
                tx_stats.retries++;
                xSemaphoreGive(credit_sem);
                vTaskDelay(1);
                continue;
            }
            seq++;
            tx_stats.packets++;
            tx_stats.bytes += len;
        }
        tx_stats.end_us = esp_timer_get_time();
        streaming = false;
        ESP_LOGI(TAG, "stream done bytes=%lu packets=%lu payload=%u us=%lld retries=%lu",
                 (unsigned long)tx_stats.bytes, (unsigned long)tx_stats.packets, tx_stats.payload,
                 (long long)(tx_stats.end_us - tx_stats.start_us), (unsigned long)tx_stats.retries);
        send_summary();
    }
}

static int tx_access(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    return BLE_ATT_ERR_UNLIKELY;
}

static int rx_access(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_UNLIKELY;
    }
    uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
    int64_t now = esp_timer_get_time();
    uint32_t seq = 0;
    if (len >= sizeof(seq)) {
        os_mbuf_copydata(ctxt->om, 0, sizeof(seq), &seq);
    }
    if (rx_stats.packets == 0) {
        rx_stats.first_us = now;
        rx_stats.first_len = len;
        rx_stats.next_seq = seq;
    }
    if (seq != rx_stats.next_seq) {
        rx_stats.seq_gaps++;
    }
    rx_stats.next_seq = seq + 1;
    rx_stats.packets++;
    rx_stats.bytes += len;
    rx_stats.last_us = now;
    return 0;
}

static int ctrl_read(struct ble_gatt_access_ctxt *ctxt)
{
    struct ble_gap_conn_desc desc = {0};
    uint8_t tx_phy = 0;
    uint8_t rx_phy = 0;
    if (conn_handle != BLE_HS_CONN_HANDLE_NONE) {
        ble_gap_conn_find(conn_handle, &desc);
        ble_gap_read_le_phy(conn_handle, &tx_phy, &rx_phy);
    }
    char buf[400];
    int n = snprintf(buf, sizeof(buf),
                     "{\"rx_bytes\":%lu,\"rx_packets\":%lu,\"rx_gaps\":%lu,\"rx_first_len\":%u,\"rx_span_us\":%lld,"
                     "\"mtu\":%u,\"itvl_1250us\":%u,\"latency\":%u,\"timeout_10ms\":%u,\"tx_phy\":%u,\"rx_phy\":%u,"
                     "\"ll_tx_octets\":%u,\"ll_tx_time\":%u,\"ll_rx_octets\":%u,\"ll_rx_time\":%u,\"encrypted\":%u}",
                     (unsigned long)rx_stats.bytes, (unsigned long)rx_stats.packets, (unsigned long)rx_stats.seq_gaps,
                     rx_stats.first_len, (long long)(rx_stats.last_us - rx_stats.first_us),
                     ble_att_mtu(conn_handle), desc.conn_itvl, desc.conn_latency, desc.supervision_timeout,
                     tx_phy, rx_phy, data_len.tx_octets, data_len.tx_time, data_len.rx_octets, data_len.rx_time,
                     desc.sec_state.encrypted);
    return os_mbuf_append(ctxt->om, buf, (uint16_t)n) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static int ctrl_write(struct ble_gatt_access_ctxt *ctxt)
{
    uint8_t cmd[40];
    uint16_t len = 0;
    if (ble_hs_mbuf_to_flat(ctxt->om, cmd, sizeof(cmd), &len) != 0 || len == 0) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    switch (cmd[0]) {
    case 'S':
        if (len < 2 || !tx_subscribed || streaming) {
            return BLE_ATT_ERR_UNLIKELY;
        }
        stream_seconds = cmd[1];
        xTaskNotifyGive(stream_task_handle);
        return 0;
    case 'Z':
        memset(&rx_stats, 0, sizeof(rx_stats));
        return 0;
    case 'E':
        return notify_bytes(cmd, len) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    case 'C': {
        if (len < 3) {
            return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
        }
        struct ble_gap_upd_params params = {
            .itvl_min = cmd[1],
            .itvl_max = cmd[2],
            .latency = 0,
            .supervision_timeout = SUPERVISION_TIMEOUT_10MS,
            .min_ce_len = 0,
            .max_ce_len = 0,
        };
        int rc = ble_gap_update_params(conn_handle, &params);
        ESP_LOGI(TAG, "conn update request min=%u max=%u rc=%d", cmd[1], cmd[2], rc);
        return rc == 0 ? 0 : BLE_ATT_ERR_UNLIKELY;
    }
    case 'P': {
        if (len < 2) {
            return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
        }
        int rc = ble_gap_set_prefered_le_phy(conn_handle, cmd[1], cmd[1], 0);
        ESP_LOGI(TAG, "phy request mask=%u rc=%d", cmd[1], rc);
        return rc == 0 ? 0 : BLE_ATT_ERR_UNLIKELY;
    }
    default:
        return BLE_ATT_ERR_UNLIKELY;
    }
}

static int ctrl_access(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        return ctrl_read(ctxt);
    }
    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return ctrl_write(ctxt);
    }
    return BLE_ATT_ERR_UNLIKELY;
}

static const struct ble_gatt_svc_def services[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &tx_uuid.u,
                .access_cb = tx_access,
                .val_handle = &tx_handle,
                .flags = BLE_GATT_CHR_F_NOTIFY,
            },
            {
                .uuid = &rx_uuid.u,
                .access_cb = rx_access,
                .flags = BLE_GATT_CHR_F_WRITE_NO_RSP | BLE_GATT_CHR_F_WRITE,
            },
            {
                .uuid = &ctrl_uuid.u,
                .access_cb = ctrl_access,
                .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE,
            },
            {0},
        },
    },
    {0},
};

static void log_conn(const char *what)
{
    struct ble_gap_conn_desc desc;
    if (ble_gap_conn_find(conn_handle, &desc) == 0) {
        ESP_LOGI(TAG, "%s itvl=%u latency=%u timeout=%u encrypted=%u", what, desc.conn_itvl,
                 desc.conn_latency, desc.supervision_timeout, desc.sec_state.encrypted);
    }
}

static int gap_event(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        ESP_LOGI(TAG, "connect status=%d", event->connect.status);
        if (event->connect.status != 0) {
            advertise();
            return 0;
        }
        conn_handle = event->connect.conn_handle;
        memset(&rx_stats, 0, sizeof(rx_stats));
        memset(&data_len, 0, sizeof(data_len));
        log_conn("connected");
        ESP_LOGI(TAG, "data len request rc=%d", ble_gap_set_data_len(conn_handle, LL_OCTETS, LL_TIME_US));
        ESP_LOGI(TAG, "phy 2M request rc=%d",
                 ble_gap_set_prefered_le_phy(conn_handle, BLE_GAP_LE_PHY_2M_MASK, BLE_GAP_LE_PHY_2M_MASK, 0));
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "disconnect reason=0x%x", event->disconnect.reason);
        conn_handle = BLE_HS_CONN_HANDLE_NONE;
        tx_subscribed = false;
        advertise();
        return 0;
    case BLE_GAP_EVENT_CONN_UPDATE:
        ESP_LOGI(TAG, "conn update status=%d", event->conn_update.status);
        log_conn("updated");
        return 0;
    case BLE_GAP_EVENT_CONN_UPDATE_REQ:
        ESP_LOGI(TAG, "peer conn update req min=%u max=%u", event->conn_update_req.peer_params->itvl_min,
                 event->conn_update_req.peer_params->itvl_max);
        return 0;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.attr_handle == tx_handle) {
            tx_subscribed = event->subscribe.cur_notify;
            ESP_LOGI(TAG, "tx subscribed=%d", tx_subscribed);
        }
        return 0;
    case BLE_GAP_EVENT_NOTIFY_TX:
        if (streaming && (event->notify_tx.status == 0 || event->notify_tx.status == BLE_HS_EDONE)) {
            xSemaphoreGive(credit_sem);
        }
        return 0;
    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "mtu=%u", event->mtu.value);
        return 0;
    case BLE_GAP_EVENT_PHY_UPDATE_COMPLETE:
        ESP_LOGI(TAG, "phy update status=%d tx=%u rx=%u", event->phy_updated.status,
                 event->phy_updated.tx_phy, event->phy_updated.rx_phy);
        return 0;
    case BLE_GAP_EVENT_DATA_LEN_CHG:
        data_len.tx_octets = event->data_len_chg.max_tx_octets;
        data_len.tx_time = event->data_len_chg.max_tx_time;
        data_len.rx_octets = event->data_len_chg.max_rx_octets;
        data_len.rx_time = event->data_len_chg.max_rx_time;
        ESP_LOGI(TAG, "data len tx=%u/%uus rx=%u/%uus", data_len.tx_octets, data_len.tx_time,
                 data_len.rx_octets, data_len.rx_time);
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
    fields.uuids128 = &svc_uuid;
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
    ESP_LOGI(TAG, "advertising rc=%d", rc);
}

static void on_sync(void)
{
    ble_hs_util_ensure_addr(0);
    ble_hs_id_infer_auto(0, &own_addr_type);
    ble_att_set_preferred_mtu(PREFERRED_MTU);
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

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(nimble_port_init());
    credit_sem = xSemaphoreCreateCounting(PIPELINE_DEPTH, 0);
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ESP_ERROR_CHECK(ble_gatts_count_cfg(services));
    ESP_ERROR_CHECK(ble_gatts_add_svcs(services));
    ble_svc_gap_device_name_set(DEVICE_NAME);
    xTaskCreate(stream_task, "stream", 4096, NULL, 5, &stream_task_handle);
    nimble_port_freertos_init(host_task);
}
