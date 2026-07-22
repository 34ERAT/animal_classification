#include <WiFi.h>
#include <ArduinoWebsockets.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

using namespace websockets;

// ---------- WiFi / Server config ----------
const char *ssid = "LINDASHAMBA";
const char *pass = "lindashamba";
const char *wsServer = "ws://your-server-address/ws";  // use ws:// not http://

WebsocketsClient client;

// ---------- Turret hardware config ----------
const int PAN_PIN = 13;
const int TILT_PIN = 12;
const int SHOOT_PIN = 14;  // relay/MOSFET controlling pump or flywheel motor

Servo panServo;
Servo tiltServo;

int panAngle = 90;   // center position
int tiltAngle = 90;  // center position

const int PAN_MIN = 0, PAN_MAX = 180;
const int TILT_MIN = 30, TILT_MAX = 150;  // adjust to your rig's safe range

bool isShooting = false;
unsigned long shootStartTime = 0;
const int SHOOT_DURATION_MS = 300;  // how long the trigger stays on per "true"

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

// ---------- Turret control ----------
void setPan(int angle) {
  angle = constrain(angle, PAN_MIN, PAN_MAX);
  panAngle = angle;
  panServo.write(panAngle);
  Serial.printf("Pan set to %d\n", panAngle);
}

void setTilt(int angle) {
  angle = constrain(angle, TILT_MIN, TILT_MAX);
  tiltAngle = angle;
  tiltServo.write(tiltAngle);
  Serial.printf("Tilt set to %d\n", tiltAngle);
}

// Non-blocking shoot: turn on now, loop() turns it off after SHOOT_DURATION_MS
void startShoot() {
  digitalWrite(SHOOT_PIN, HIGH);
  isShooting = true;
  shootStartTime = millis();
  Serial.println("Shoot triggered");
}

void updateShoot() {
  if (isShooting && (millis() - shootStartTime >= SHOOT_DURATION_MS)) {
    digitalWrite(SHOOT_PIN, LOW);
    isShooting = false;
    Serial.println("Shoot released");
  }
}

// ---------- Command parsing ----------
// Expects a JSON array: ["LEFT", 15, false]  →  [direction, angle, shoot]
void handleCommand(const String &msg) {
  StaticJsonDocument<128> doc;
  DeserializationError err = deserializeJson(doc, msg);

  if (err) {
    Serial.print("JSON parse failed: ");
    Serial.println(err.c_str());
    return;
  }

  if (!doc.is<JsonArray>() || doc.size() < 3) {
    Serial.println("Malformed command, expected [direction, angle, shoot]");
    return;
  }

  String direction = doc[0].as<String>();
  int angle = doc[1].as<int>();
  bool shootFlag = doc[2].as<bool>();

  switch (direction) {
    case "LEFT":
      setPan(panAngle - angle);
      break;
    case "RIGHT":
      setPan(panAngle + angle);
      break;
    case "UP":
      setTilt(tiltAngle + angle);
      break;
    case "DOWN":
      setTilt(tiltAngle - angle);
      break;
    default:
      Serial.println("Unrecognized direction: " + direction);
  }

  if (shootFlag) {
    startShoot();
  }
}

// ---------- Websocket callbacks ----------
void onMessageCallback(WebsocketsMessage message) {
  Serial.print("Command received: ");
  Serial.println(message.data());
  handleCommand(message.data());
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

void setup() {
  Serial.begin(115200);

  // Servo setup
  panServo.attach(PAN_PIN);
  tiltServo.attach(TILT_PIN);
  setPan(panAngle);
  setTilt(tiltAngle);

  // Shoot pin setup
  pinMode(SHOOT_PIN, OUTPUT);
  digitalWrite(SHOOT_PIN, LOW);

  // WiFi + Websocket setup
  connectToWifi();
  Serial.println("WiFi connection successful.");
  Serial.println("Attempting to connect to server...");

  client.onMessage(onMessageCallback);
  client.onEvent(onEventCallback);
  client.connect(wsServer);
  client.send("hello master!");
  client.ping();
}

void loop() {
  client.poll();
  updateShoot();
}
