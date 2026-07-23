#include <WiFi.h>
#include <ArduinoWebsockets.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

//// Defines pins
#define PAN_PIN 12
#define TILT_PIN 16
#define TRIGGER_PIN 13
#define RELOAD1_PIN 26
#define RELOAD2_PIN 27

// Define Constans
#define PAN_MIN 0
#define PAN_MAX 180
#define TILT_MIN 30
#define TILT_MAX 150

using namespace websockets;

// ---------- WiFi / Server config ----------
const char *ssid = "Ithink";
const char *pass = "ilove2capples";
const char *wsServer = "ws://192.168.0.103:8765/ws";  // use ws:// not http://

WebsocketsClient client;
Servo panServo;
Servo tiltServo;
Servo trigger;

int panAngle = 90;   // center position
int tiltAngle = 90;  // center position


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
void shoot() {
  trigger.write(45);
  isShooting = true;
  shootStartTime = millis();
  Serial.println("trigger pullled");
  delay(500);
}

void reloadGun() {
  if (isShooting && (millis() - shootStartTime >= SHOOT_DURATION_MS)) {
    trigger.write(0);
    isShooting = false;
    Serial.println("staring reload");

    // reload
    digitalWrite(RELOAD1_PIN, HIGH);
    delay(1000);
    digitalWrite(RELOAD1_PIN, LOW);
    digitalWrite(RELOAD1_PIN, HIGH);
    delay(1000);
    digitalWrite(RELOAD1_PIN, LOW);
    delay(2000);
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

  // if (direction == "LEFT") {
  //   setPan(panAngle - angle);
  // } else if (direction == "RIGHT") {
  //   setPan(panAngle + angle);
  // } else if (direction == "UP") {
  //   setTilt(tiltAngle + angle);
  // } else if (direction == "DOWN") {
  //   setTilt(tiltAngle - angle);
  // } else {
  //   Serial.println("Unrecognized direction: " + direction);
  // }
  if (shootFlag) {
    shoot();
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

const int stopValue = 90;  // Calibrate this for your specific servo

// Smoothly ramp servo speed from one value to another over a duration
void rampServo(Servo &servo, int fromSpeed, int toSpeed, int durationMs) {
  int steps = 7;  // Number of increments for smoothness
  int stepDelay = durationMs / steps;

  for (int i = 0; i <= steps; i++) {
    int value = fromSpeed + ((toSpeed - fromSpeed) * i) / steps;
    servo.write(value);
    delay(stepDelay);
  }
}

void dummyPanning() {
  // Ramp up to pan right (slow start)
  rampServo(panServo, stopValue, 60, 400);
  delay(800);  // Hold at panning speed (simulates steady pan)

  // Ramp down to a stop (slow finish, like settling on a subject)
  rampServo(panServo, 60, stopValue, 400);
  delay(600);  // Pause, as if "looking" at something

  // Ramp up to pan left
  rampServo(panServo, stopValue, 120, 400);
  delay(1000);  // Hold — longer pan this direction

  // Ramp down to a stop
  rampServo(panServo, 120, stopValue, 400);
  delay(500);  // Pause again

  // Quick small pan back toward center (like a subtle re-adjustment)
  rampServo(panServo, stopValue, 75, 200);
  delay(300);
  rampServo(panServo, 75, stopValue, 200);
}

void setup() {
  Serial.begin(115200);

  ////REload Pins setup
  pinMode(RELOAD1_PIN, OUTPUT);
  pinMode(RELOAD2_PIN, OUTPUT);

  // Dummy paning
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);

  panServo.setPeriodHertz(50);
  panServo.attach(PAN_PIN, 500, 2400);
  trigger.attach(TRIGGER_PIN);

  dummyPanning();

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
  reloadGun();
}
