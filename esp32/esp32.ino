#include <WiFi.h>
#include <ArduinoWebsockets.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

//// Defines pins
#define PAN_PIN 12
#define TILT_PIN 16
#define TRIGGER_PIN 13
#define RELOAD1_PIN 27
#define RELOAD2_PIN 26

// Define Constans
#define PAN_MIN 0
#define PAN_MAX 180
#define TILT_MIN 30
#define TILT_MAX 150
#define TRIGGER_START_POINT 50
#define TRIGGER_SHOOT 0

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


enum GunState { IDLE,
                SHOOTING,
                RELOAD_DELAY_1,
                RELOAD_DELAY_2,
                RELOAD_DELAY_3,
                COOLDOWN };
GunState gunState = IDLE;
unsigned long stateStartTime = 0;
const int SHOOT_DURATION_MS = 500;      // how long the trigger stays on per "true"
const int COOLDOWN_DURATION_MS = 2000;  // adjust this to taste

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

// Non-blocking shoot: turn on now, updateGun() turns it off
void shoot() {
  if (gunState != IDLE) {
    return;  // Ignore if already shooting or reloading
  }
  trigger.write(TRIGGER_SHOOT);
  gunState = SHOOTING;
  stateStartTime = millis();
  Serial.println("trigger pullled");
}

void updateGun() {
  unsigned long now = millis();

  if (gunState == SHOOTING && (now - stateStartTime >= SHOOT_DURATION_MS)) {
    trigger.write(TRIGGER_START_POINT);
    Serial.println("starting reload");
    digitalWrite(RELOAD2_PIN, HIGH);
    gunState = RELOAD_DELAY_1;
    stateStartTime = now;

  } else if (gunState == RELOAD_DELAY_1 && (now - stateStartTime >= 750)) {
    digitalWrite(RELOAD2_PIN, LOW);
    digitalWrite(RELOAD1_PIN, HIGH);
    gunState = RELOAD_DELAY_2;
    stateStartTime = now;

  } else if (gunState == RELOAD_DELAY_2 && (now - stateStartTime >= 240)) {
    digitalWrite(RELOAD1_PIN, LOW);
    Serial.println("reload complete, cooling down");
    gunState = COOLDOWN;
    stateStartTime = now;

  } else if (gunState == COOLDOWN && (now - stateStartTime >= COOLDOWN_DURATION_MS)) {
    gunState = IDLE;
    Serial.println("ready to shoot");
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

  //// Reload Pins setup
  pinMode(RELOAD1_PIN, OUTPUT);
  pinMode(RELOAD2_PIN, OUTPUT);

  // 1. Reset trigger to rest position first
  trigger.attach(TRIGGER_PIN);
  trigger.write(TRIGGER_START_POINT);
  delay(300);  // give servo time to reach position before moving on

  // 2. Init pan servo and run dummy panning routine
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);

  panServo.setPeriodHertz(50);
  panServo.attach(PAN_PIN, 500, 2400);

  dummyPanning();

  // 3. WiFi + Websocket setup
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
  updateGun();
}
