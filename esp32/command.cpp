#include <cmath>
#include "HardwareSerial.h"
#include "Turrent.h"
#include <ArduinoJson.h>
void handleCommand(const String &msg, Turrent &turrent) {
  StaticJsonDocument<128> doc;
  DeserializationError err = deserializeJson(doc, msg);

  if (err) {
    Serial.print("JSON parse failed: ");
    Serial.println(err.c_str());
    return;
  }

  if (!doc.is<JsonArray>() || doc.size() < 2) {
    Serial.println("Malformed command, expected [X Y]");
    return;
  }
  int x = doc[0].as<int>();
  int y = doc[1].as<int>();
  int center_tolerance = 40;
  int offSetX = x - turrent.getFrameWidht() / 2;
  int offSetY = y - turrent.getFrameHeight() / 2;
  //pan angel = offset * (field of view x / w)
  int  angleX = (int) round( offSetX * (65.0f / turrent.getFrameWidht()));
  int angleY = (int)round(offSetY * (65.0f / turrent.getFrameHeight()));
 // Serial.println(turrent.getFrameHeight());
  Serial.printf("X: %d, Y: %d\n", angleX, angleY);
  
  turrent.pan(angleX < 0 ? abs(angleX)+ PAN_CENTER: angleX );
  turrent.tilt(angleY< 0 ? abs(angleY) + TILT_CENTER : angleY);
}
