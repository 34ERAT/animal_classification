#include <WiFi.h>
#include <ArduinoWebsockets.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>
#include "commands.h"
#include "Turrent.h"

using namespace websockets;


Turrent turrent(45, 0, 5);
// ---------- WiFi / Server config ----------
const char *ssid = "Ithink";
const char *pass = "ilove2capples";
const char *wsServer = "ws://192.168.0.105:8765/ws";  // use ws:// not http://

WebsocketsClient client;


// ---------- WiFi setup ----------
void connectToWifi() {
  Serial.print("Connecting to WiFi SSID: ");
  Serial.println(ssid);

  WiFi.begin(ssid, pass);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.println("WiFi connected!");
}


// ---------- Websocket callbacks ----------
void onMessageCallback(WebsocketsMessage message) {
  Serial.print("Command received: ");
  //Serial.println(message.data());
  handleCommand(message.data(), turrent);
}

void onEventCallback(WebsocketsEvent event, String data) {
  switch (event) {
    case WebsocketsEvent::ConnectionOpened:
      Serial.println("Connection Opened");
            break;
    case WebsocketsEvent::ConnectionClosed:
      Serial.println("Connection Closed");
      break;
    case WebsocketsEvent::GotPing:
      Serial.println("Got a ping");
      break;
    case WebsocketsEvent::GotPong:
      Serial.println("Got a pong");
      break;
  }
}
void turrentCalibrate() {
  turrent.pan(0);
  delay(500);
  turrent.pan(PAN_CENTER);
  delay(500);
  turrent.pan(180);

  //tilt test

  turrent.tilt(0);
  delay(500);
  turrent.tilt(TILT_CENTER);
  delay(500);
  turrent.tilt(180);
  delay(500);

  turrent.tilt(TILT_CENTER);
  turrent.pan(PAN_CENTER);
}

void setup() {
  Serial.begin(115200);
  turrent.setFrame(640, 480);
  turrentCalibrate();
  // turrent = new Turrent(45, 0, 5);
  // 3. WiFi + Websocket setup

  connectToWifi();
  client.onMessage(onMessageCallback);
  client.onEvent(onEventCallback);
  client.connect(wsServer);
  Serial.println("Connect to web socket");
  client.send("Read for commands!");
}

void loop() {
  client.poll();
}
