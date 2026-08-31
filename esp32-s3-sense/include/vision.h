#pragma once

#include "FaceDetector.h"

// ============================================================================
// Gesichtserkennung auf KERN 0.
//
// Warum das ein eigenes Modul ist: FaceDetector_Detect() lief bis 2026-08-31
// mitten in loop(). Die Augen zeichnen aber IN loop(), also stand das Bild
// genau so lange still, wie die Inferenz brauchte. Mit
// FACE_DETECT_INTERVAL_MS 300 war das ein sichtbares Stocken etwa zweimal je
// Sekunde - vor dem Umbau gemessen (Gesicht im Bild, 100 % Trefferquote):
//
//   loop_hz 11 29 31 35 32 42 26 41 33 17 36 31 40 26 42 33 29 41 32 ...
//   3 von 55 Abtastungen unter 15 Hz, also sichtbar eingefroren
//
// Nach dem Umzug auf Kern 0, gleiche Sitzung, gleicher Abstand:
//
//   loop_hz 29 29 29 27 27 29 29 29 29 21 29 29 25 28 28 29 29 29 29 ...
//   0 von 58 Abtastungen unter 15 Hz
//
// Der Mittelwert aendert sich dabei kaum (30,2 -> 28,3 Hz), und das ist der
// Punkt: gemessen wurde nicht der Mittelwert, sondern die STREUUNG. Ein
// Blickziel gibt es seither alle 181 ms statt alle 382 ms.
//
// Der Arduino-Loop haengt auf Kern 1 (CONFIG_ARDUINO_RUNNING_CORE=1), Kern 0
// ist ausser im OTA-Update-Modus (WLAN) unbeschaeftigt. Also liegt dort jetzt
// die gesamte Kette Bild holen -> Inferenz -> veroeffentlichen, und loop()
// liest nur noch das letzte Ergebnis ab.
//
// GETEILTER ZUSTAND, und der ist der gefaehrliche Teil. FaceResult_t wird von
// Kern 0 geschrieben und von Kern 1 gelesen. Beide Seiten fassen es nur unter
// derselben portMUX_TYPE an, und latest() kopiert die ganze Struktur in einem
// Stueck: die State-Machine und die Telemetrie sehen damit garantiert DENSELBEN
// Treffer und nicht ein halb ueberschriebenes Gemisch aus zwei Durchlaeufen.
// Ein Feld einzeln zu lesen waere genau die sporadische Fehlerart, die dieses
// Projekt Tage kostet.
// ============================================================================

namespace vision {

// Startet die Erkennungsaufgabe auf Kern 0. Erst NACH FaceDetector_Init()
// aufrufen - schlaegt der Init fehl, wird hier nichts gestartet.
// Gibt false zurueck, wenn die Aufgabe nicht angelegt werden konnte.
bool begin();

// Erkennung an- oder abschalten. Im UPDATE-Modus aus: dort laeuft das WLAN auf
// Kern 0, und die Eule ignoriert Gesichter ohnehin. Beim Abschalten wird das
// veroeffentlichte Ergebnis GELEERT, sonst zeigte die Telemetrie den letzten
// Treffer noch minutenlang waehrend des Flashens.
void setEnabled(bool enabled);

// Letztes Ergebnis abholen. Kopiert unter der Sperre, also in sich schluessig.
// Billig (~40 Byte) und darf jede Runde aufgerufen werden.
void latest(FaceResult_t& out);

}  // namespace vision
