#pragma once
// Die Brücke zwischen Verhaltenszustand und Servo-Zielen (Ohren, Kopf, Fluegel).
// Nur im Bewegungs-Image (SERVO_MOTION_ENABLED=1) wirksam; im gesperrten Image ist
// update() ein sofortiger Rueckkehrpunkt. Logik und Tests: lib/ServoMotion/OwlChoreo.
namespace motion {

// Einmal pro loop()-Iteration NACH behavior::update() und VOR servos.update().
void update();

// Spielt eine Koerpergeste ("startle", "happy", "negative", "curious") ab. Gibt true zurueck, wenn
// sie angenommen wurde; false bei unbekanntem Namen, laufender Geste, Cooldown, gesperrtem Image
// oder Ruhezustand (Schlaf, Boot, Fehler, OTA). Aufruf aus dem Protokoll (core 1, aus loop()).
bool react(const char* name);

}  // namespace motion
