#!/usr/bin/env python3
"""Kontrollierter Zweiphasen-Flash des Servo-Sicherheitsabbilds (esptool 5.3).

backup: USB-JTAG-Reset DIREKT in ROM, kompletten Flash sichern, in ROM bleiben.
flash:  DIREKT in ROM verbinden, Partitionen/Backup/Abbild pruefen, beide
        OTA-Slots schreiben/verifizieren; NVS/Bootloader unveraendert pruefen.
        Erst nach allen Pruefungen das neue Abbild starten.

SHA256 muss aus dem geprueften Build stammen. Ein Hash allein beweist keine
Servo-Sicherheit! Nur das nativ getestete Sicherheitsabbild verwenden.
Brain/Monitor vorher stoppen. Keine automatische Rueckkehr zur alten Firmware.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct


def digest(data):
    return hashlib.sha256(data).hexdigest()


def partitions(data):
    found = {}
    for pos in range(0, 0xC00, 32):
        entry = data[pos:pos+32]
        if entry[:2] != b'\xaa\x50':
            break
        _, kind, subtype, offset, size, label, _ = struct.unpack('<HBBII16sI', entry)
        found[label.split(b'\0')[0].decode()] = (kind, subtype, offset, size)
    expected = {'nvs': (1, 2, 0x9000, 0x6000),
                'otadata': (1, 0, 0xF000, 0x2000),
                'ota_0': (0, 0x10, 0x20000, 0x3F0000),
                'ota_1': (0, 0x11, 0x410000, 0x3F0000)}
    for label, value in expected.items():
        if found.get(label) != value:
            raise RuntimeError('Unerwartete Partition %s: %r; NICHT geschrieben.' % (label, found.get(label)))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['backup', 'flash'])
    parser.add_argument('--port', required=True)
    parser.add_argument('--backup', required=True, type=Path)
    parser.add_argument('--no-stub', action='store_true',
                        help='Langsamer ROM-Transport bei instabilem USB-Bulk-Read')
    parser.add_argument('--image', type=Path)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    os.umask(0o077)
    if args.phase == 'backup':
        if args.backup.exists():
            raise RuntimeError('Backup existiert bereits; nicht ueberschreiben.')
        args.backup.parent.mkdir(parents=True, exist_ok=True)
    else:
        if args.image is None or not args.sha256:
            parser.error('flash braucht --image und --sha256')
        image = args.image.read_bytes()
        if digest(image) != args.sha256 or not 0 < len(image) <= 0x3F0000:
            raise RuntimeError('Abbild-Hash/Groesse stimmt nicht; USB bleibt unberuehrt.')
        backup = args.backup.read_bytes()
        manifest = json.loads(args.backup.with_suffix('.json').read_text())
        if digest(backup) != manifest['sha256']:
            raise RuntimeError('Backup-Hash stimmt nicht.')
        partitions(backup[0x8000:0x9000])

    import serial
    from esptool.targets.esp32s3 import ESP32S3ROM
    from esptool.cmds import read_flash, write_flash, verify_flash, run_stub, attach_flash

    stream = serial.Serial(port=None, baudrate=115200, timeout=1, exclusive=True)
    stream.dtr = False
    stream.rts = False
    stream.port = args.port
    try:
        stream.open()
        esp = ESP32S3ROM(stream)
        # Kein generischer DTR/RTS-Reset: dieses Board hat nur USB-JTAG.
        esp.connect('usb-reset', attempts=2)
        # Download-Modus auch ueber Port-Schliessen/Warmreset festhalten.
        # Erst hard_reset() NACH Verifikation loescht diesen S3-Latch wieder.
        esp.write_reg(esp.RTC_CNTL_OPTION1_REG,
                      esp.RTC_CNTL_FORCE_DOWNLOAD_BOOT_MASK,
                      esp.RTC_CNTL_FORCE_DOWNLOAD_BOOT_MASK)
        if not args.no_stub:
            esp = run_stub(esp)
        attach_flash(esp)
        flash_size = 1 << ((esp.flash_id() >> 16) & 0xFF)
        if not 8*1024*1024 <= flash_size <= 32*1024*1024:
            raise RuntimeError('Unerwartete Flashgroesse; kein Schreibzugriff.')
        if args.phase == 'backup':
            # Kleine Transaktionen statt eines mehr-MB-USB-Streams; ein
            # abgebrochener Read darf niemals als vollstaendiges Backup gelten.
            blocks = []
            for offset in range(0, flash_size, 0x10000):
                blocks.append(read_flash(esp, offset, 0x10000, no_progress=True))
            data = b''.join(blocks)
            args.backup.write_bytes(data)
            args.backup.with_suffix('.json').write_text(json.dumps({
                'size': len(data), 'sha256': digest(data),
                'partitions': partitions(data[0x8000:0x9000]),
            }, indent=2)+'\n')
            print('BACKUP VERIFIZIERBAR:', digest(data), 'ROM bleibt aktiv.', flush=True)
            return
        if len(backup) != flash_size:
            raise RuntimeError('Backupgroesse passt nicht zur Hardware.')
        before = read_flash(esp, 0, 0x20000, no_progress=True)
        if before != backup[:0x20000]:
            raise RuntimeError('Bootloader/Partitionen/NVS seit Backup veraendert; Abbruch.')
        pairs = [(0x20000, image), (0x410000, image)]
        write_flash(esp, pairs, flash_size='keep')
        verify_flash(esp, pairs)
        after = read_flash(esp, 0, 0x20000, no_progress=True)
        if before != after:
            raise RuntimeError('Bootloader/Partitionen/NVS stimmen nicht mehr; KEIN Start!')
        print('BEIDE Slots verifiziert; Bootloader/Partitionen/NVS unveraendert. Starte Sicherheitsabbild.', flush=True)
        esp.hard_reset()
    finally:
        # Kein Reset bei Fehlern: ROM/Stub bleibt stehen, nie altes Abbild booten.
        stream.close()


if __name__ == '__main__':
    main()
