#!/usr/bin/env bash
#
# install.sh — (re)deploy the Robot Owl Brain on an Orange Pi Zero 3W.
#
# This is the LIGHT installer: it refreshes the Python brain only. Use it after
# you have changed brain code and want to redeploy WITHOUT touching the audio
# stack (the owl_i2s kernel module, the DT overlay, and asound.conf are handled
# by setup.sh and only need redoing if the firmware or hardware changes).
#
# What it does:
#   1. Copies this orangepi-brain/ tree to /opt/robot-owl/orangepi-brain
#      (idempotent; keeps the installed .venv, models/ and audio/ in place).
#   2. Refreshes the virtualenv + installs requirements.txt.
#   3. Ensures the 'robotowl' user (in dialout/audio/i2c/gpio groups) exists.
#   4. Installs the udev rules for the ESP32 USB CDC port and the GPIO chips.
#   5. Installs + enables the systemd units (brain, power button, fallback hotspot)
#      and restarts them.
#
# Run as root (or with sudo). Re-running is safe. For a FIRST-TIME install
# (audio stack + config wizard + model download + reboot), run ./setup.sh.
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # .../orangepi-brain
DEST_DIR="/opt/robot-owl/orangepi-brain"
UNIT_SRC="${SRC_DIR}/deploy/robot-owl-brain.service"
UDEV_SRC="${SRC_DIR}/deploy/99-robot-owl-serial.rules"
UDEV_DEST="/etc/udev/rules.d/99-robot-owl-serial.rules"
UNIT_DEST="/etc/systemd/system/robot-owl-brain.service"
POWER_UNIT_SRC="${SRC_DIR}/deploy/robot-owl-power.service"
POWER_UNIT_DEST="/etc/systemd/system/robot-owl-power.service"
HOTSPOT_UNIT_SRC="${SRC_DIR}/deploy/robot-owl-hotspot.service"
HOTSPOT_UNIT_DEST="/etc/systemd/system/robot-owl-hotspot.service"
GPIO_RULE_SRC="${SRC_DIR}/deploy/99-robot-owl-gpio.rules"
GPIO_RULE_DEST="/etc/udev/rules.d/99-robot-owl-gpio.rules"
SERVICE_USER="robotowl"

log()  { printf '\n\033[1;32m[robot-owl]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[robot-owl] WARN:\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[robot-owl] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo (needs root for user/udev/systemd)"

log "1/5 Copying orangepi-brain to ${DEST_DIR}"
mkdir -p "${DEST_DIR}"
# Copy source, but never clobber the venv, the downloaded models, or the audio
# stack (those live under DEST_DIR and are owned by setup.sh).
rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'deploy' --exclude 'audio' --exclude 'models' --exclude 'config.yaml' \
    "${SRC_DIR}/" "${DEST_DIR}/"

[ -f "${DEST_DIR}/config.yaml" ] || install -m 0644 "${SRC_DIR}/config.yaml" "${DEST_DIR}/config.yaml"

log "2/5 Refreshing virtualenv + requirements"
if [ ! -x "${DEST_DIR}/.venv/bin/python" ]; then
    python3 -m venv "${DEST_DIR}/.venv"
fi
"${DEST_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null
CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-2}" \
    "${DEST_DIR}/.venv/bin/pip" install -r "${DEST_DIR}/requirements.txt"

log "3/5 Ensuring '${SERVICE_USER}' user exists (in dialout/audio groups)"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --user-group --home "${DEST_DIR}" --shell /usr/sbin/nologin \
        --comment "Robot Owl brain" "${SERVICE_USER}"
    chown -R "${SERVICE_USER}:" "${DEST_DIR}"
fi
getent group i2c >/dev/null || groupadd --system i2c
getent group gpio >/dev/null || groupadd --system gpio
usermod -aG dialout,audio,i2c,gpio "${SERVICE_USER}"
chown -R "${SERVICE_USER}:" "${DEST_DIR}"

log "4/5 Installing udev rules for the ESP32 serial port and the GPIO chips (mute switch)"
install -m 0644 "${UDEV_SRC}" "${UDEV_DEST}"
install -m 0644 "${GPIO_RULE_SRC}" "${GPIO_RULE_DEST}"
systemctl daemon-reload
udevadm control --reload-rules || true
udevadm trigger --subsystem-match=gpio || true

log "5/5 Installing + enabling systemd units (brain + power button + hotspot)"
install -m 0644 "${UNIT_SRC}" "${UNIT_DEST}"
install -m 0644 "${POWER_UNIT_SRC}" "${POWER_UNIT_DEST}"
install -m 0644 "${HOTSPOT_UNIT_SRC}" "${HOTSPOT_UNIT_DEST}"
systemctl daemon-reload
systemctl enable robot-owl-brain.service robot-owl-power.service robot-owl-hotspot.service
systemctl restart robot-owl-power.service || warn "power button restart failed — check 'journalctl -u robot-owl-power -f'"
# The hotspot never touches wlan0 (it adds uap0 next to it), so restarting it over SSH is safe.
systemctl restart robot-owl-hotspot.service || warn "hotspot restart failed — check 'journalctl -u robot-owl-hotspot -f'"
systemctl restart robot-owl-brain.service || warn "service restart failed — check 'journalctl -u robot-owl-brain -f'"

log "Done."
log "Watch logs:  journalctl -u robot-owl-brain -f"
log "Status:      systemctl status robot-owl-brain"
log "Audio card:  aplay -l  (should list the 'owl' card; if not, re-run ./setup.sh)"
