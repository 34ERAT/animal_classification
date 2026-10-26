#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_camera.h"

// ---------- CONFIG ----------
const char* WIFI_SSID = "A";
const char* WIFI_PASS = "12345678";
const char* SERVER_IP = "192.168.101.233";
const uint16_t SERVER_PORT = 5005;

const framesize_t FRAME_SIZE = FRAMESIZE_VGA;  // QVGA/VGA/SVGA
const int JPEG_QUALITY = 12;                   // 10-63, lower = better quality/larger
const size_t CHUNK_SIZE = 1400;                // UDP payload per packet

// ---------- AI THINKER ESP32-CAM PINS ----------
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM   0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM     5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

// ---------- GLOBALS ----------
WiFiUDP udp;
IPAddress serverIP;
uint16_t frameId = 0;

struct __attribute__((packed)) Header {
  uint16_t frameId;
  uint16_t chunkIdx;
  uint16_t chunkCnt;
};

// ---------- CAMERA ----------
void powerCycleCamera() {
  pinMode(PWDN_GPIO_NUM, OUTPUT);
  digitalWrite(PWDN_GPIO_NUM, HIGH);  // camera off (power down)
  delay(150);
  digitalWrite(PWDN_GPIO_NUM, LOW);   // camera on
  delay(250);                         // allow sensor power to stabilize
}

esp_err_t tryInitCamera(int xclkHz, int pwdnPin) {
  // Enable internal pullups on SCCB (I2C) lines to ensure stable communication
  gpio_set_pull_mode((gpio_num_t)SIOD_GPIO_NUM, GPIO_PULLUP_ONLY);
  gpio_set_pull_mode((gpio_num_t)SIOC_GPIO_NUM, GPIO_PULLUP_ONLY);

  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = pwdnPin;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = xclkHz;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_LATEST;

  if (psramFound()) {
    config.frame_size = FRAME_SIZE;
    config.jpeg_quality = JPEG_QUALITY;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    Serial.println("WARNING: PSRAM not found, using reduced settings (QVGA)");
    config.frame_size = FRAMESIZE_QVGA;
    config.jpeg_quality = 15;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  return esp_camera_init(&config);
}

bool initCamera() {
  struct AttemptConfig {
    int xclk;
    int pwdn;
    const char* desc;
  } attempts[] = {
    {20000000, PWDN_GPIO_NUM, "XCLK 20 MHz, PWDN GPIO 32 (Standard AI-Thinker)"},
    {20000000, -1,            "XCLK 20 MHz, PWDN -1 (Clone / Hardwired GND)"},
    {10000000, PWDN_GPIO_NUM, "XCLK 10 MHz, PWDN GPIO 32"},
    {10000000, -1,            "XCLK 10 MHz, PWDN -1"},
  };

  for (size_t i = 0; i < sizeof(attempts)/sizeof(attempts[0]); i++) {
    Serial.printf("Camera probe attempt %d: %s...\n", (int)i + 1, attempts[i].desc);

    powerCycleCamera();
    esp_err_t err = tryInitCamera(attempts[i].xclk, attempts[i].pwdn);

    if (err == ESP_OK) {
      sensor_t* s = esp_camera_sensor_get();
      Serial.printf("Camera OK! Sensor PID: 0x%02X\n", s ? s->id.PID : 0);
      return true;
    }

    Serial.printf("Probe failed (err 0x%x)\n", err);
    delay(200);
  }
  return false;
}

// ---------- WIFI ----------
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  WiFi.setSleep(false);  // lower latency, steadier throughput

  Serial.print("Connecting to WiFi");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
    if (millis() - start > 20000) {
      Serial.println("\nWiFi timeout, restarting...");
      ESP.restart();
    }
  }
  Serial.printf("\nConnected, IP: %s\n", WiFi.localIP().toString().c_str());
}

// ---------- SETUP / LOOP ----------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\nESP32-CAM UDP streamer");

  if (!initCamera()) {
    Serial.println("Camera init failed on all attempts.");
    Serial.println("Check: ribbon cable seated, 5V/1A+ supply, PSRAM enabled in Tools menu.");
    while (true) delay(1000);
  }

  connectWiFi();
  serverIP.fromString(SERVER_IP);
}

void loop() {
  static unsigned long lastReport = 0;
  static uint32_t framesSent = 0;

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi lost, reconnecting...");
    WiFi.disconnect();
    connectWiFi();
  }

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    delay(10);
    return;
  }

  uint16_t chunkCnt = (fb->len + CHUNK_SIZE - 1) / CHUNK_SIZE;

  for (uint16_t i = 0; i < chunkCnt; i++) {
    size_t offset = (size_t)i * CHUNK_SIZE;
    size_t len = min(CHUNK_SIZE, fb->len - offset);

    Header h = { frameId, i, chunkCnt };

    udp.beginPacket(serverIP, SERVER_PORT);
    udp.write((uint8_t*)&h, sizeof(h));
    udp.write(fb->buf + offset, len);
    udp.endPacket();

    if ((i & 0x07) == 0x07) delayMicroseconds(500);  // avoid overflowing the TX buffer
  }

  frameId++;
  framesSent++;
  esp_camera_fb_return(fb);

  if (millis() - lastReport >= 5000) {
    Serial.printf("FPS: %.1f\n", framesSent * 1000.0 / (millis() - lastReport));
    framesSent = 0;
    lastReport = millis();
  }
}
