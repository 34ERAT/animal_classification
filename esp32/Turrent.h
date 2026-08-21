#ifndef TURRENT_H
#define TURRENT_H
#include <Arduino.h>
#include <ESP32Servo.h>
#define PAN_SERVO 12
#define TILT_SERVO 14
#define TRIGGER_SERVO 13
#define PAN_CENTER 90
#define TILT_CENTER 90



class Turrent {
private:
  bool shooting;
  Servo tiltServo;
  Servo panServo;
  Servo triggerServo;
  Servo Reload;
  int maxLoad;
  int triggerMax;
  int triggerMin;
  uint frameHieght;
  uint frameWidht;
  

public:
  Turrent(int triggerMax, int triggerMin, int maxLoad);

  void setFrame( uint frameWidht, uint frameHieght);
  int getFrameHeight();
  int getFrameWidht();

  void pan(int deg);

  void tilt(int deg);

  void shoot();

  void reload(int maxLoad);
};
#endif
