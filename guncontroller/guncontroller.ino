#include <Servo.h>

#define TRIGGER_PIN 7
#define TILT_PIN    6
#define PAN_PIN     5
#define CMD_DELIM   ","

// ─────────────────────────────────────────────────────────────────────────────
// TUNING SECTION: everything you need to adjust lives here.
//
// The vision model always sends angles in 0-180, where 90 means "dead center".
// For each axis:
//   CENTER = servo angle used when the model says 90 (mechanical trim)
//   SWEEP  = physical degrees the servo moves for the model's FULL 0-180 range
//            (smaller = more shrinking; 180 = no shrinking)
//   DIR    = +1 or -1, flip if the axis moves the wrong way
//   MIN/MAX = hard mechanical safety limits, the servo never goes past these
// ─────────────────────────────────────────────────────────────────────────────

const int   PAN_CENTER  = 100;
const float PAN_SWEEP   = 70.0;
const int   PAN_DIR     = 1;
const int   PAN_MIN     = 0;
const int   PAN_MAX     = 180;

const int   TILT_CENTER = 90;
const float TILT_SWEEP  = 60.0;   // matches your old 60-120 tilt mapping
const int   TILT_DIR    = 1;
const int   TILT_MIN    = 60;
const int   TILT_MAX    = 120;

const int   MODEL_CENTER = 90;    // what the model treats as "centered"

// ─────────────────────────────────────────────────────────────────────────────

char   commandBuffer[64];
size_t indexPos   = 0;
bool   overflowed = false;
Servo  tiltServo, panServo;

// Convert the model's angle (0-180) into a servo angle:
// shrink the offset from model center, pivot around the servo's center,
// then clamp to the hard limits.
int scaleAngle(int modelAngle, int center, float sweep, int dir, int minA, int maxA) {
  modelAngle = constrain(modelAngle, 0, 180);
  float offset = modelAngle - MODEL_CENTER;                 // -90 .. +90
  int out = center + dir * (int)lroundf(offset * sweep / 180.0f);
  return constrain(out, minA, maxA);
}

// Fire the trigger briefly (100 ms pulse). This is the only intentional delay
// and it only runs when shoot_status == 1 (locked-on target).
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

  int tilt = scaleAngle(values[0], TILT_CENTER, TILT_SWEEP, TILT_DIR, TILT_MIN, TILT_MAX);
  int pan  = scaleAngle(values[1], PAN_CENTER,  PAN_SWEEP,  PAN_DIR,  PAN_MIN,  PAN_MAX);

  tiltServo.write(tilt);
  panServo.write(pan);

  // Non-blocking: reply immediately so the PC vision loop is never stalled.
  Serial.println("OK");

  // Trigger only on explicit lock-on (after ACK so Python isn't stalled)
  if (values[2] == 1) shoot();
}

void setup() {
  Serial.begin(115200);
  tiltServo.attach(TILT_PIN);
  panServo.attach(PAN_PIN);
  tiltServo.write(TILT_CENTER);
  panServo.write(PAN_CENTER);
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
