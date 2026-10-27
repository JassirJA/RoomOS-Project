/*
 * ROOM OS — ESP32 CSI Receiver (Arduino IDE Version)
 * Direct Link Mode (ESP-NOW / Promiscuous) — No Router or Wi-Fi Network Required!
 *
 * Hardware: Standard ESP32 DevKit V1
 * Baud Rate: 921600 baud
 */

#include <WiFi.h>
#include <esp_now.h>
#include "esp_wifi.h"
#include "rom/ets_sys.h"

/* ========================================================================= */
/*                         RF SENSING CONFIGURATION                          */
/* ========================================================================= */
#define WIFI_CHANNEL        11    // Must match Transmitter Channel
#define BUILTIN_LED_PIN     2     // Built-in LED on ESP32 DevKit V1
/* ========================================================================= */

// Dedicated Room OS Transmitter MAC address to lock onto
static const uint8_t ROOM_OS_TX_MAC[6] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};

static volatile uint32_t s_csi_packet_count = 0;
static volatile int8_t s_last_rssi = 0;
static uint32_t s_last_health_time = 0;
static uint32_t s_prev_packet_count = 0;

/* CSI Hardware Interrupt Callback */
void wifiCsiCallback(void *ctx, wifi_csi_info_t *data) {
  if (!data || !data->buf || data->len < 64) return;

  // Filter: strictly lock onto ROOM_OS_TX_MAC (1a:00:00:00:00:00)
  if (memcmp(data->mac, ROOM_OS_TX_MAC, 6) != 0) {
    return;
  }

  wifi_pkt_rx_ctrl_t *rx_ctrl = &data->rx_ctrl;
  s_csi_packet_count++;
  s_last_rssi = rx_ctrl->rssi;

  // Header line on very first packet
  if (s_csi_packet_count == 1) {
    ets_printf("type,seq,mac,rssi,rate,noise_floor,channel,timestamp,sig_len,rx_state,len,first_word,data\n");
  }

  // Stream Room OS standard CSV frame:
  // CSI_DATA,<seq>,<mac>,<rssi>,<rate>,<noise_floor>,<channel>,<timestamp>,<sig_len>,<rx_state>,<len>,<first_word>,"[<im0>,<re0>,...]"
  ets_printf("CSI_DATA,%lu,%02x:%02x:%02x:%02x:%02x:%02x,%d,%d,%d,%d,%lu,%d,%d,%d,%d,\"[",
             (unsigned long)s_csi_packet_count,
             data->mac[0], data->mac[1], data->mac[2],
             data->mac[3], data->mac[4], data->mac[5],
             rx_ctrl->rssi,
             rx_ctrl->rate,
             rx_ctrl->noise_floor,
             rx_ctrl->channel,
             (unsigned long)rx_ctrl->timestamp,
             rx_ctrl->sig_len,
             rx_ctrl->sig_mode,
             data->len,
             data->first_word_invalid);

  // Stream raw interleaved I/Q array: [im0, re0, im1, re1, ...]
  for (int i = 0; i < data->len; i++) {
    ets_printf(i == 0 ? "%d" : ",%d", data->buf[i]);
  }
  ets_printf("]\"\n");
}

void setup() {
  // High-speed serial stream (921600 baud) for Room OS Engine
  Serial.begin(921600);
  pinMode(BUILTIN_LED_PIN, OUTPUT);
  digitalWrite(BUILTIN_LED_PIN, LOW);
  delay(1000);

  Serial.println("\n==================================================");
  Serial.println("   ROOM OS — ESP32 CSI Receiver (Direct RF)       ");
  Serial.println("   Mode: Router-Free Direct CSI Capture           ");
  Serial.println("==================================================");

  // 1. Initialize Wi-Fi in Station Mode (Disconnected / No Router)
  WiFi.mode(WIFI_STA);
  esp_wifi_start();

  // 2. Lock Wi-Fi radio to Channel 11
  esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);

  // 3. Initialize ESP-NOW on receiver to process ESP-NOW frames
  if (esp_now_init() == ESP_OK) {
    esp_now_peer_info_t peerInfo = {};
    memset(peerInfo.peer_addr, 0xff, 6);
    peerInfo.channel = WIFI_CHANNEL;
    peerInfo.ifidx = WIFI_IF_STA;
    peerInfo.encrypt = false;
    esp_now_add_peer(&peerInfo);

    esp_now_rate_config_t rate_cfg = {
        .phymode = WIFI_PHY_MODE_HT20,
        .rate = WIFI_PHY_RATE_MCS0_LGI,
        .ersu = false,
        .dcm = false,
    };
    esp_now_set_peer_rate_config(peerInfo.peer_addr, &rate_cfg);
  }

  // 4. Enable Promiscuous Mode with all packet types enabled
  wifi_promiscuous_filter_t filter = {
    .filter_mask = WIFI_PROMIS_FILTER_MASK_ALL
  };
  esp_wifi_set_promiscuous_filter(&filter);
  esp_wifi_set_promiscuous(true);

  // 5. Configure CSI hardware registers on ESP32
  wifi_csi_config_t csi_config = {
    .lltf_en           = 1,
    .htltf_en          = 1,
    .stbc_htltf2_en    = 1,
    .ltf_merge_en      = 1,
    .channel_filter_en = 0,   // Accept frames without strict frequency rejection
    .manu_scale        = 0,
    .shift             = 0,
  };

  ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
  ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(&wifiCsiCallback, NULL));
  ESP_ERROR_CHECK(esp_wifi_set_csi(true));

  digitalWrite(BUILTIN_LED_PIN, HIGH);
  Serial.printf("CSI Capture active on Channel %d (Direct Link). Streaming to Room OS...\n", WIFI_CHANNEL);
  s_last_health_time = millis();
}

void loop() {
  uint32_t now = millis();

  // Broadcast Room OS health heartbeat every 1 second
  if (now - s_last_health_time >= 1000) {
    uint32_t count = s_csi_packet_count;
    uint32_t delta = count - s_prev_packet_count;
    s_prev_packet_count = count;
    float pkt_rate = (float)delta / ((now - s_last_health_time) / 1000.0f);
    s_last_health_time = now;

    ets_printf("ROOM_OS_HEALTH,RX_ONLINE,pkts_total:%lu,rate_hz:%.1f,last_rssi:%d\n",
               (unsigned long)count,
               pkt_rate,
               (int)s_last_rssi);
  }

  delay(50);
}
