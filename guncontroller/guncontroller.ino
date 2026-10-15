#include <Servo.h>

#define TRIGGER_PIN 7
#define TILT_PIN 6
#define PAN_PIN 5
#define CMD_DELIM ","

char commandBuffer[64];
size_t indexPos = 0;
bool overflowed = false;
Servo tiltServo, panServo;

void shoot() {
  digitalWrite(TRIGGER_PIN, HIGH);
  delay(100);
  digitalWrite(TRIGGER_PIN, LOW);
}

void processCommand(char *cmd) {
  int values[3];
  int count = 0;

  char *token = strtok(cmd, CMD_DELIM);
  while (token != NULL && count < 3) {
    values[count++] = atoi(token);
    token = strtok(NULL, CMD_DELIM);
  }

  if (count != 3 || token != NULL) {
    Serial.println("ERR expected tilt_angle,pan_angle,shoot_status");
    return;
  }

  int tilt = map(constrain(values[0], 0, 180), 0, 180, 60, 120);
  int pan  = constrain(values[1], 0, 180);

  tiltServo.write(tilt);
  panServo.write(pan);
  delay(400);                 // enough for servos to settle; tune as needed
  if (values[2] == 1) shoot();

  Serial.println("OK");       // tell the PC we're done
}

void setup() {
  Serial.begin(115200);
  tiltServo.attach(TILT_PIN);
  panServo.attach(PAN_PIN);
  tiltServo.write(90);
  panServo.write(90);
  pinMode(TRIGGER_PIN, OUTPUT);
  digitalWrite(TRIGGER_PIN, LOW);
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\r') continue;

    if (c == '\n') {
      if (overflowed) {
        Serial.println("ERR command too long");
        overflowed = false;
      } else if (indexPos > 0) {
        commandBuffer[indexPos] = '\0';
        processCommand(commandBuffer);
      }
      indexPos = 0;
    } else if (!overflowed) {
      if (indexPos < sizeof(commandBuffer) - 1) commandBuffer[indexPos++] = c;
      else overflowed = true;   // discard the rest until newline
    }
  }
}
