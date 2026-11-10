#pragma once
// Die Brücke zwischen Verhaltenszustand und Servo-Zielen (Ohren, Kopf, Fluegel).
// Nur im Bewegungs-Image (SERVO_MOTION_ENABLED=1) wirksam; im gesperrten Image ist
// update() ein sofortiger Rueckkehrpunkt. Logik und Tests: lib/ServoMotion/OwlChoreo.
#include <stdint.h>

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

// Manuelle Bewegung (Weboberflaeche): haelt EINEN Servo 10 s auf `value`, in den Einheiten der
// Servoprofile - Ohren/Fluegel 0..100, Kopf Grad (positiv = links). Laeuft durch dieselbe
// Begrenzung wie die Choreografie (OwlChoreo -> ServoMotion: Kalibrierungsgrenzen, Rampen).
// false bei unbekanntem Kanal, gesperrtem Image oder Ruhezustand (Schlaf, Boot, Fehler, OTA).
// Ohne Bewegungs-Image: servos.setAngle() wie frueher (im gesperrten Image also immer false).
bool manual(uint8_t channel, float value);

}  // namespace motion
