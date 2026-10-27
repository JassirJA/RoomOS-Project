/*
 * ROOM OS — Dedicated ESP32 Receiver Firmware
 * "Don't watch the room. Sense the room."
 *
 * Hardware: Standard ESP32 (Single node RX)
 * Function: Captures CSI frames emitted by Room OS Transmitter, formats them,
 *           and streams them over USB Serial to the Laptop Room OS Engine.
 * Topology: Direct Device-to-Device (No external router).
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "rom/ets_sys.h"
#include "nvs_flash.h"
#include "esp_mac.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"

// Configuration fallbacks if not built via Kconfig
#ifndef CONFIG_ROOM_OS_WIFI_CHANNEL
#define CONFIG_ROOM_OS_WIFI_CHANNEL 11
#endif

#if defined(CONFIG_ROOM_OS_WIFI_BW_HT20)
#define ROOM_OS_BANDWIDTH WIFI_BW_HT20
#define ROOM_OS_SECOND_CHAN WIFI_SECOND_CHAN_NONE
#define ROOM_OS_PHY_MODE WIFI_PHY_MODE_HT20
#else
#define ROOM_OS_BANDWIDTH WIFI_BW_HT40
#define ROOM_OS_SECOND_CHAN WIFI_SECOND_CHAN_BELOW
#define ROOM_OS_PHY_MODE WIFI_PHY_MODE_HT40
#endif

static const char *TAG = "ROOM_OS_RX";

// Filter only packets sent from the Room OS Transmitter MAC
static const uint8_t ROOM_OS_TX_MAC[6] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};

static volatile uint32_t s_csi_packet_count = 0;
static volatile int8_t s_last_rssi = 0;
static volatile uint32_t s_last_timestamp = 0;

static void wifi_csi_rx_callback(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf || info->len == 0) {
        return;
    }

    // Filter packets: only process frames from the Room OS Transmitter
    if (memcmp(info->mac, ROOM_OS_TX_MAC, 6) != 0) {
        return;
    }

    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;
    s_csi_packet_count++;
    s_last_rssi = rx_ctrl->rssi;
    s_last_timestamp = rx_ctrl->timestamp;

    // Header line on very first packet to announce columns
    if (s_csi_packet_count == 1) {
        ets_printf("type,seq,mac,rssi,rate,noise_floor,channel,timestamp,sig_len,rx_state,len,first_word,data\n");
    }

    // Format: CSI_DATA,<seq>,<mac>,<rssi>,<rate>,<noise_floor>,<channel>,<timestamp>,<sig_len>,<rx_state>,<len>,<first_word>,"[<im0>,<re0>,...]"
    ets_printf("CSI_DATA,%lu,%02x:%02x:%02x:%02x:%02x:%02x,%d,%d,%d,%d,%lu,%d,%d,%d,%d,\"[",
               (unsigned long)s_csi_packet_count,
               info->mac[0], info->mac[1], info->mac[2],
               info->mac[3], info->mac[4], info->mac[5],
               rx_ctrl->rssi,
               rx_ctrl->rate,
               rx_ctrl->noise_floor,
               rx_ctrl->channel,
               (unsigned long)rx_ctrl->timestamp,
               rx_ctrl->sig_len,
               rx_ctrl->sig_mode,
               info->len,
               info->first_word_invalid);

    // Stream raw I/Q array: info->buf contains [Im0, Re0, Im1, Re1, ...]
    for (int i = 0; i < info->len; i++) {
        ets_printf(i == 0 ? "%d" : ",%d", info->buf[i]);
    }
    ets_printf("]\"\n");
}

static void wifi_init_receiver(void)
{
    ESP_LOGI(TAG, "Initializing Room OS Receiver on Channel %d (BW: %s)...",
             CONFIG_ROOM_OS_WIFI_CHANNEL,
             (ROOM_OS_BANDWIDTH == WIFI_BW_HT40) ? "HT40" : "HT20");

    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init());

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, ROOM_OS_BANDWIDTH));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_ROOM_OS_WIFI_CHANNEL, ROOM_OS_SECOND_CHAN));

    // Enable Promiscuous Mode for raw frame interception
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

    // Configure CSI hardware for ESP32
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = true,
        .stbc_htltf2_en    = true,
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = false,
        .shift             = false,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_callback, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));

    ESP_LOGI(TAG, "CSI capture enabled. Listening for Room OS TX (%02x:%02x:%02x:%02x:%02x:%02x)...",
             ROOM_OS_TX_MAC[0], ROOM_OS_TX_MAC[1], ROOM_OS_TX_MAC[2],
             ROOM_OS_TX_MAC[3], ROOM_OS_TX_MAC[4], ROOM_OS_TX_MAC[5]);
}

static void health_monitor_task(void *pvParameters)
{
    uint32_t prev_count = 0;
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(2000));
        uint32_t current_count = s_csi_packet_count;
        uint32_t delta = current_count - prev_count;
        prev_count = current_count;

        float pkt_rate = (float)delta / 2.0f;
        // Broadcast health frame parsed by Room OS Engine
        ets_printf("ROOM_OS_HEALTH,RX_ONLINE,pkts_total:%lu,rate_hz:%.1f,last_rssi:%d\n",
                   (unsigned long)current_count,
                   pkt_rate,
                   (int)s_last_rssi);
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "========================================");
    ESP_LOGI(TAG, " ROOM OS — RECEIVER INITIALIZING        ");
    ESP_LOGI(TAG, " Target: Standard ESP32                 ");
    ESP_LOGI(TAG, "========================================");

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_receiver();

    xTaskCreatePinnedToCore(health_monitor_task, "health_monitor", 2048, NULL, 1, NULL, 0);
}
