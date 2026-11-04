#pragma once
// Die Brücke zwischen Verhaltenszustand und Servo-Zielen (Ohren, Kopf, Fluegel).
// Nur im Bewegungs-Image (SERVO_MOTION_ENABLED=1) wirksam; im gesperrten Image ist
// update() ein sofortiger Rueckkehrpunkt. Logik und Tests: lib/ServoMotion/OwlChoreo.
namespace motion {

// Einmal pro loop()-Iteration NACH behavior::update() und VOR servos.update().
void update();

// Zeigt eine Stimmung mit Koerper: ihre Augen plus eine Geste oder gehaltene Pose (Ohren, Fluegel,
// Kopfneigung). Namen: die Stimmungen der Weboberflaeche (happy, angry, skeptic ...) und die alten
// Gestennamen startle/negative/curious. Gibt true zurueck, wenn sie angenommen wurde; false bei
// unbekanntem Namen, gesperrtem Image, Ruhezustand (Schlaf, Boot, Fehler, OTA) oder - nur ohne
// force - laufender Geste/Cooldown. force=true: Aufruf von Hand/Brain (Cooldown und laufende Geste
// ueberspringen); der Sensor-Pfad (Doppelklopfen) ruft ohne force. Aus loop() (core 1).
bool react(const char* name, bool force = false);

}  // namespace motion
