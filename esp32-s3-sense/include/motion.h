#pragma once
// Die Brücke zwischen Verhaltenszustand und Servo-Zielen (Ohren, Kopf, Fluegel).
// Nur im Bewegungs-Image (SERVO_MOTION_ENABLED=1) wirksam; im gesperrten Image ist
// update() ein sofortiger Rueckkehrpunkt. Logik und Tests: lib/ServoMotion/OwlChoreo.
namespace motion {

// Einmal pro loop()-Iteration NACH behavior::update() und VOR servos.update().
void update();

}  // namespace motion
