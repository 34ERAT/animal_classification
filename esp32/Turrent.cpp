#include <Arduino.h>
#include "Turrent.h"


Turrent::Turrent(int triggerMax, int triggerMin, int maxLoad) {
  shooting = false;
  tiltServo.attach(TILT_SERVO);
  panServo.attach(PAN_SERVO);
  triggerServo.attach(TRIGGER_SERVO);
  this->triggerMax = triggerMax;
  this->triggerMin = triggerMin;
  triggerServo.write(this->triggerMin);
}

void Turrent::pan(int deg) {
  panServo.write(deg);
}

void Turrent::tilt(int deg) {
  tiltServo.write(deg);
}

void Turrent::shoot() {
  if (shooting && maxLoad > 0) return;
  triggerServo.write(triggerMax);  // pull the trigger to shoot
  delay(100);
  triggerServo.write(triggerMin);  // release the trigger
  maxLoad--;
}

void Turrent::reload(int maxLoad) {
  this->maxLoad = maxLoad;
}



void Turrent::setFrame(uint frameWidht, uint frameHieght) {
  this->frameHieght = frameHieght;
  this->frameWidht = frameWidht;
}
int Turrent::getFrameWidht() {
  return this->frameWidht;
}
int Turrent::getFrameHeight() {
  return this->frameHieght;
}
