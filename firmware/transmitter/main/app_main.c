/*
 * ROOM OS — Dedicated ESP32 Transmitter Firmware
 * "Don't watch the room. Sense the room."
 *
 * Hardware: Standard ESP32 (Single node TX)
 * Function: Continuous controlled packet transmission for Wi-Fi CSI sensing.
 * Topology: Direct Device-to-Device (No external router).
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
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

#ifndef CONFIG_ROOM_OS_TX_RATE_HZ
#define CONFIG_ROOM_OS_TX_RATE_HZ 30
#endif

#if defined(CONFIG_ROOM_OS_WIFI_BW_HT40)
#define ROOM_OS_BANDWIDTH WIFI_BW_HT40
#define ROOM_OS_SECOND_CHAN WIFI_SECOND_CHAN_BELOW
#define ROOM_OS_PHY_MODE WIFI_PHY_MODE_HT40
#else
#define ROOM_OS_BANDWIDTH WIFI_BW_HT20
#define ROOM_OS_SECOND_CHAN WIFI_SECOND_CHAN_NONE
#define ROOM_OS_PHY_MODE WIFI_PHY_MODE_HT20
#endif

static const char *TAG = "ROOM_OS_TX";

// Dedicated Room OS Transmitter MAC (allows receiver to lock onto this source)
static const uint8_t ROOM_OS_TX_MAC[6] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};

// Broadcast address for ESP-NOW frames
static const uint8_t ROOM_OS_PEER_MAC[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

typedef struct __attribute__((packed)) {
    uint32_t magic;      // 'ROMS' (0x534D4F52)
    uint32_t seq;        // Incremental sequence number
    uint32_t timestamp;  // Uptime in milliseconds
    uint8_t  node_id;    // 0x01 for TX #1
    uint8_t  reserved[3];
} room_os_packet_t;

static uint32_t s_seq = 0;
static uint32_t s_packets_sent = 0;
static uint32_t s_send_errors = 0;

static void wifi_init_transmitter(void)
{
    ESP_LOGI(TAG, "Initializing Room OS Transmitter on Channel %d (BW: %s)...",
             CONFIG_ROOM_OS_WIFI_CHANNEL,
             (ROOM_OS_BANDWIDTH == WIFI_BW_HT40) ? "HT40" : "HT20");

    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init());

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, ROOM_OS_TX_MAC));

    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, ROOM_OS_BANDWIDTH));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_ROOM_OS_WIFI_CHANNEL, ROOM_OS_SECOND_CHAN));

#if defined(CONFIG_ROOM_OS_TX_POWER_MAX)
    // 20dBm maximum transmit power
    esp_wifi_set_max_tx_power(80);
#endif

    // Setup ESP-NOW
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk((const uint8_t *)"room_os_key_1234"));

    esp_now_peer_info_t peer = {
        .channel = CONFIG_ROOM_OS_WIFI_CHANNEL,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
    };
    memcpy(peer.peer_addr, ROOM_OS_PEER_MAC, 6);
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    esp_now_rate_config_t rate_cfg = {
        .phymode = ROOM_OS_PHY_MODE,
        .rate = WIFI_PHY_RATE_MCS0_LGI,
        .ersu = false,
        .dcm = false,
    };
    esp_now_set_peer_rate_config(peer.peer_addr, &rate_cfg);

    ESP_LOGI(TAG, "Transmitter Wi-Fi ready. Direct MAC: %02x:%02x:%02x:%02x:%02x:%02x",
             ROOM_OS_TX_MAC[0], ROOM_OS_TX_MAC[1], ROOM_OS_TX_MAC[2],
             ROOM_OS_TX_MAC[3], ROOM_OS_TX_MAC[4], ROOM_OS_TX_MAC[5]);
}

static void tx_worker_task(void *pvParameters)
{
    room_os_packet_t pkt;
    pkt.magic = 0x534D4F52; // 'ROMS'
    pkt.node_id = 0x01;
    memset(pkt.reserved, 0, sizeof(pkt.reserved));

    const TickType_t delay_ticks = pdMS_TO_TICKS(1000 / CONFIG_ROOM_OS_TX_RATE_HZ);
    TickType_t last_wake_time = xTaskGetTickCount();

    uint32_t last_stat_print = xTaskGetTickCount();

    ESP_LOGI(TAG, "Starting TX packet loop at %d Hz...", CONFIG_ROOM_OS_TX_RATE_HZ);

    while (1) {
        pkt.seq = ++s_seq;
        pkt.timestamp = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);

        esp_err_t err = esp_now_send(ROOM_OS_PEER_MAC, (const uint8_t *)&pkt, sizeof(pkt));
        if (err == ESP_OK) {
            s_packets_sent++;
        } else {
            s_send_errors++;
        }

        // Periodic status log every 5 seconds
        if ((xTaskGetTickCount() - last_stat_print) >= pdMS_TO_TICKS(5000)) {
            ESP_LOGI(TAG, "[HEARTBEAT] Sent: %lu pkts | Errors: %lu | Target Rate: %d Hz",
                     (unsigned long)s_packets_sent,
                     (unsigned long)s_send_errors,
                     CONFIG_ROOM_OS_TX_RATE_HZ);
            last_stat_print = xTaskGetTickCount();
        }

        vTaskDelayUntil(&last_wake_time, (delay_ticks > 0) ? delay_ticks : 1);
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "========================================");
    ESP_LOGI(TAG, " ROOM OS — TRANSMITTER INITIALIZING     ");
    ESP_LOGI(TAG, " Target: Standard ESP32                 ");
    ESP_LOGI(TAG, "========================================");

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_transmitter();

    xTaskCreatePinnedToCore(tx_worker_task, "tx_worker", 4096, NULL, 5, NULL, 0);
}
