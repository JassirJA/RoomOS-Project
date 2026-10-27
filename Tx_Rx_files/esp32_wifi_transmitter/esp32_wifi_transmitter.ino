/*
 * ROOM OS — ESP32 Transmitter (Arduino IDE Version)
 * Direct Link Mode (ESP-NOW) — No Router or Wi-Fi Network Required!
 *
 * Hardware: Standard ESP32 DevKit V1
 * Function: Broadcasts continuous 100 Hz RF packets for CSI spatial sensing.
 */

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

/* ========================================================================= */
/*                         RF TRANSMISSION SETTINGS                          */
/* ========================================================================= */
#define WIFI_CHANNEL        11    // 2.4GHz Wi-Fi Channel (1 to 14)
#define TX_RATE_HZ          15    // Packets per second (15 Hz = ~66.7ms interval)
#define BUILTIN_LED_PIN     2     // Built-in LED on ESP32 DevKit V1
/* ========================================================================= */

// Dedicated Room OS Transmitter MAC address
static const uint8_t ROOM_OS_TX_MAC[6]   = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x00};
static const uint8_t BROADCAST_PEER_MAC[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

typedef struct __attribute__((packed)) {
    uint32_t magic;      // 'ROMS' (0x534D4F52)
    uint32_t seq;        // Incremental sequence number
    uint32_t timestamp;  // Uptime in milliseconds
    uint8_t  node_id;    // 0x01 for TX Node #1
    uint8_t  reserved[3];
} room_os_packet_t;

static room_os_packet_t s_packet;
static uint32_t s_seq = 0;
static uint32_t s_packets_sent = 0;
static uint32_t s_send_errors = 0;
static uint32_t s_last_stat_time = 0;

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(20);
  pinMode(BUILTIN_LED_PIN, OUTPUT);
  digitalWrite(BUILTIN_LED_PIN, LOW);
  delay(1000);

  Serial.println("\n==================================================");
  Serial.println("  ROOM OS — ESP32 Transmitter (Direct ESP-NOW)   ");
  Serial.println("  Mode: Router-Free Direct RF Transmission        ");
  Serial.println("==================================================");

  // Initialize Wi-Fi in Station Mode (Disconnected / No Router)
  WiFi.mode(WIFI_STA);
  esp_wifi_set_mac(WIFI_IF_STA, (uint8_t *)ROOM_OS_TX_MAC);
  esp_wifi_start();

  // Lock Wi-Fi channel
  esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);

  Serial.print("Transmitter MAC: ");
  Serial.println(WiFi.macAddress());

  // Initialize ESP-NOW
  if (esp_now_init() != ESP_OK) {
    Serial.println("Error initializing ESP-NOW!");
    return;
  }

  // Register broadcast peer
  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, BROADCAST_PEER_MAC, 6);
  peerInfo.channel = WIFI_CHANNEL;
  peerInfo.ifidx = WIFI_IF_STA;
  peerInfo.encrypt = false;
  
  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("Failed to register ESP-NOW peer");
    return;
  }

  // Set transmission rate to 802.11n HT20 MCS0 (OFDM modulation required for CSI)
  esp_now_rate_config_t rate_cfg = {
      .phymode = WIFI_PHY_MODE_HT20,
      .rate = WIFI_PHY_RATE_MCS0_LGI,
      .ersu = false,
      .dcm = false,
  };
  esp_now_set_peer_rate_config(peerInfo.peer_addr, &rate_cfg);

  // Prepare packet payload header
  s_packet.magic = 0x534D4F52; // 'ROMS'
  s_packet.node_id = 0x01;
  memset(s_packet.reserved, 0, sizeof(s_packet.reserved));

  digitalWrite(BUILTIN_LED_PIN, HIGH);
  Serial.printf("Transmitting on Channel %d at %d Hz (No Router Needed)...\n", WIFI_CHANNEL, TX_RATE_HZ);
  s_last_stat_time = millis();
}

static uint32_t s_current_tx_rate = TX_RATE_HZ;

void check_serial_commands() {
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.startsWith("CMD:PROBE=HIGH_RATE") || cmd.indexOf("RATE=200") >= 0) {
      s_current_tx_rate = 200;
      Serial.println("[TX PROBE ACTIVE] Switching to 200 Hz verification probe mode!");
    } else if (cmd.startsWith("CMD:PROBE=NORMAL") || cmd.indexOf("RATE=100") >= 0) {
      s_current_tx_rate = 100;
      Serial.println("[TX NORMAL] Resuming 100 Hz baseline transmission mode.");
    }
  }
}

void loop() {
  check_serial_commands();

  s_packet.seq = ++s_seq;
  s_packet.timestamp = millis();

  // Send packet directly to receiver over RF
  esp_err_t result = esp_now_send(BROADCAST_PEER_MAC, (uint8_t *)&s_packet, sizeof(s_packet));
  if (result == ESP_OK) {
    s_packets_sent++;
  } else {
    s_send_errors++;
  }

  // Periodic heartbeat log every 3 seconds
  uint32_t now = millis();
  if (now - s_last_stat_time >= 3000) {
    float rate = (float)s_packets_sent / ((now - s_last_stat_time) / 1000.0f);
    Serial.printf("[TX Active] Sent: %u pkts | Rate: %.1f Hz (Target: %u Hz) | Ch: %d | MAC: %s | Errors: %u\n", 
                  s_packets_sent, rate, s_current_tx_rate, WIFI_CHANNEL, WiFi.macAddress().c_str(), s_send_errors);
    s_packets_sent = 0;
    s_send_errors = 0;
    s_last_stat_time = now;
    digitalWrite(BUILTIN_LED_PIN, !digitalRead(BUILTIN_LED_PIN)); // Flash LED
  }

  // Dynamic transmission interval based on current active rate
  uint32_t delay_ms = (s_current_tx_rate > 0) ? (1000 / s_current_tx_rate) : 10;
  delay(delay_ms);
}
