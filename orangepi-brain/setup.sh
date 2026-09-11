#!/usr/bin/env bash
#
# setup.sh — one-shot installer for the Robot Owl on an Orange Pi Zero 3W
# (Allwinner A733) running DietPi (Debian).
#
# Run it once after installing the OS (and wiring the owl):
#
#     cd orangepi-brain
#     sudo ./setup.sh
#
# It will:
#   0. Pre-flight checks (root, apt, required files, kernel headers).
#   1. Install system packages (python3-venv, alsa-utils, rsync, portaudio,
#      the kernel-module build tools, and dtc for the device-tree overlay).
#   2. Build + install the owl_i2s kernel module (the A733 I2S sound card that
#      drives the MAX98357A amp and captures the ICS43434 mic).
#   3. Compile + install + enable the owl-i2s DT overlay (muxes the I2S0 pins
#      and registers the sound card). Needs a reboot to take effect.
#   4. Install the ALSA mixer config (asound.conf) for the shared 48 kHz card.
#   5. Install the brain to /  6. Run an INTERACTIVE CONFIG WIZARD (serial port, web
#      UI, speech, auto-sleep) and write the choices to config.yaml.
#   7. Pre-download the whisper.cpp model (and note the emotion GGUF) so the
#      first real transcription is instant instead of a several-minute fetch.
#   8. Create a dedicated 'robotowl' user + a udev rule for the ESP32 port.
#   9. Install + enable the systemd service.
#   10. Reboot to apply the DT overlay (which loads the sound card). The robot
#       starts automatically on the next boot.
#
# The script is idempotent — safe to re-run (re-running after the reboot just
# re-applies everything and clears the one-shot hook).
#
# Non-interactive use (unattended / scripted installs):
#     sudo ./setup.sh --non-interactive
#   skips the wizard and keeps the bundled config.yaml defaults.
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../orangepi-brain
DEST_DIR="/opt/robot-owl/orangepi-brain"
AUDIO_DIR="${SRC_DIR}/audio"
UNIT_SRC="${SRC_DIR}/deploy/robot-owl-brain.service"
UDEV_SRC="${SRC_DIR}/deploy/99-robot-owl-serial.rules"
UDEV_DEST="/etc/udev/rules.d/99-robot-owl-serial.rules"
UNIT_DEST="/etc/systemd/system/robot-owl-brain.service"
MODULE_LOAD_CONF="/etc/modules-load.d/robot-owl-i2s.conf"
SERVICE_USER="robotowl"
OVERLAY_NAME="owl-i2s-overlay"
WHISPER_MODEL_BASE="https://huggingface.co/ggerganov/whisper-models/resolve/main"

log()  { printf '\n\033[1;32m[robot-owl]\033[0m %s\n' "$*"; }
info() { printf '\033[0;37m[robot-owl]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[robot-owl] WARN:\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[robot-owl] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo:  sudo ./setup.sh"

# ----------------------------------------------------------------------------
# Options: --non-interactive skips the config wizard (keeps bundled defaults).
# ----------------------------------------------------------------------------
NON_INTERACTIVE=0
for arg in "$@"; do
    case "${arg}" in
        --non-interactive|-n) NON_INTERACTIVE=1 ;;
        *) die "unknown argument '${arg}' (only --non-interactive is supported)" ;;
    esac
done
# The wizard prompts on /dev/tty so it still works under `sudo` (stdin is not
# the user's terminal there). If there is no TTY (cron/CI), fall back to
# non-interactive automatically.
if [ "${NON_INTERACTIVE}" -eq 1 ]; then
    INTERACTIVE=0
elif [ -t /dev/tty ]; then
    INTERACTIVE=1
else
    info "No TTY detected — running non-interactive (bundled config defaults)."
    INTERACTIVE=0
fi

ask() {
    # ask <prompt-with-default> <varname>   ->  sets the variable to the answer
    local prompt="${1}" varname="${2}" default reply
    default=$(printf '%s' "${prompt}" | sed -n 's/.* \[\(.*\)\]$/\1/p')
    if [ -t /dev/tty ]; then
        read -r -t 180 -p "${prompt} " reply < /dev/tty || reply="${default}"
    else
        reply="${default}"
    fi
    [ -z "${reply}" ] && reply="${default}"
    printf -v "${varname}" '%s' "${reply}"
}

# ----------------------------------------------------------------------------
run_config_wizard() {
    log "Config wizard (Enter accepts the default shown in [brackets])"

    # ---- Serial: discover the ESP32 USB CDC port ---------------------------
    local port="/dev/ttyACM0" first
    first=$(ls /dev/ttyACM* 2>/dev/null | head -1)
    if [ -n "${first}" ]; then
        port="${first}"
        info "Detected USB CDC port: ${first}"
    else
        info "No /dev/ttyACM* device found right now (fine if the ESP32 is not"
        info "plugged in yet)."
    fi
    if [ "${INTERACTIVE}" -eq 1 ]; then
        ask "Serial port for the ESP32 [${port}]" SERIAL_PORT
        ask "Serial baudrate [115200]" BAUDRATE
        ask "Serial read timeout (seconds) [1]" SERIAL_TIMEOUT
    else
        SERIAL_PORT="${port}"; BAUDRATE=115200; SERIAL_TIMEOUT=1
    fi

    # ---- Web UI -------------------------------------------------------------
    if [ "${INTERACTIVE}" -eq 1 ]; then
        ask "Enable the web UI (test panel at http://<owl-ip>:8080)? [false]" WEB_ENABLED
        ask "Web UI port [8080]" WEB_PORT
    else
        WEB_ENABLED="false"; WEB_PORT=8080
    fi

    # ---- Speech recognition -------------------------------------------------
    # The mic is the ICS43434 on the shared I2S card (the 'owl' ALSA card). It
    # only exists after the DT overlay is applied (post-reboot), so it cannot be
    # probed here — leave mic_device empty (= the default card).
    local speech_enabled="false" speech_model="small" speech_lang="de"
    if [ "${INTERACTIVE}" -eq 1 ]; then
        ask "Enable speech recognition (I2S mic -> whisper.cpp)? [false]" speech_enabled
        ask "Whisper.cpp model (tiny/base/small/medium; small = best accuracy) [small]" speech_model
        ask "Spoken language (ISO code) [de]" speech_lang
    fi
    [ -z "${speech_model}" ] && speech_model="small"
    [ -z "${speech_lang}" ] && speech_lang="de"

    # ---- Autonomous sleep ---------------------------------------------------
    local auto_sleep_enabled="false" auto_sleep_after=60
    if [ "${INTERACTIVE}" -eq 1 ]; then
        ask "Owl falls asleep automatically when idle? [false]" auto_sleep_enabled
        if [ "${auto_sleep_enabled}" = "true" ]; then
            ask "Sleep after how many seconds with no face / tap / speech? [60]" auto_sleep_after
        fi
    fi

    # ---- Apply the choices to the source config.yaml -----------------------
    info "Writing choices to ${SRC_DIR}/config.yaml ..."
    _set_serial "${SERIAL_PORT}" "${BAUDRATE}" "${SERIAL_TIMEOUT}"
    _set_web "${WEB_ENABLED}" "${WEB_PORT}"
    # speech.model is the model SIZE here; the download step (7) rewrites it to
    # the absolute path of the fetched file.
    _set_speech "${speech_enabled}" "${speech_model}" "${speech_lang}" ""
    _set_auto_sleep "${auto_sleep_enabled}" "${auto_sleep_after}"

    info "Config summary:"
    echo "    serial.port        = ${SERIAL_PORT} (baud ${BAUDRATE}, timeout ${SERIAL_TIMEOUT}s)"
    echo "    web.enabled        = ${WEB_ENABLED} (port ${WEB_PORT})"
    echo "    speech.enabled     = ${speech_enabled}"
    if [ "${speech_enabled}" = "true" ]; then
        echo "    speech.model       = ${speech_model} (lang ${speech_lang})"
    fi
    echo "    auto_sleep.enabled = ${auto_sleep_enabled}"
    if [ "${auto_sleep_enabled}" = "true" ]; then
        echo "    auto_sleep.after_s   = ${auto_sleep_after}"
    fi
}

# (Nested-key setters. Surgical line edits so config.yaml's comments survive.)
_set_serial() {
    local file="${SRC_DIR}/config.yaml" start end
    start=$(grep -n "^serial:" "${file}" | head -1 | cut -d: -f1)
    end=$(awk -v s="${start}" 'NR>s && /^[^[:space:]]/ { print NR; exit }' "${file}")
    [ -z "${end}" ] && end=$(wc -l < "${file}")
    awk -v s="${start}" -v e="${end}" -v p="$1" -v b="$2" -v t="$3" '
        NR>=s+1 && NR<e {
            if ($0 ~ /^  port:/)            { print "  port: " p; next }
            if ($0 ~ /^  baudrate:/)        { print "  baudrate: " b; next }
            if ($0 ~ /^  timeout:/)         { print "  timeout: " t; next }
        } { print }' "${file}" > "${file}.tmp" && mv "${file}.tmp" "${file}"
}
_set_web() {
    local file="${SRC_DIR}/config.yaml" start end
    start=$(grep -n "^web:" "${file}" | head -1 | cut -d: -f1)
    end=$(awk -v s="${start}" 'NR>s && /^[^[:space:]]/ { print NR; exit }' "${file}")
    [ -z "${end}" ] && end=$(wc -l < "${file}")
    awk -v s="${start}" -v e="${end}" -v en="$1" -v p="$2" '
        NR>=s+1 && NR<e {
            if ($0 ~ /^  enabled:/) { print "  enabled: " en; next }
            if ($0 ~ /^  port:/)    { print "  port: " p; next }
        } { print }' "${file}" > "${file}.tmp" && mv "${file}.tmp" "${file}"
}
_set_speech() {
    local file="${SRC_DIR}/config.yaml" start end
    local enabled="$1" model="$2" lang="$3" mic="$4"
    start=$(grep -n "^speech:" "${file}" | head -1 | cut -d: -f1)
    end=$(awk -v s="${start}" 'NR>s && /^[^[:space:]]/ { print NR; exit }' "${file}")
    [ -z "${end}" ] && end=$(wc -l < "${file}")
    awk -v s="${start}" -v e="${end}" -v en="${enabled}" -v m="${model}" -v l="${lang}" -v mic="${mic}" '
        NR>=s+1 && NR<e {
            if ($0 ~ /^  enabled:/)      { print "  enabled: " en; next }
            if ($0 ~ /^  model:/)        { if (m != "") { print "  model: \"" m "\""; next } }
            if ($0 ~ /^  language:/)     { print "  language: " l; next }
            if ($0 ~ /^  mic_device:/)   { if (mic != "") { print "  mic_device: \"" mic "\""; next } }
        } { print }' "${file}" > "${file}.tmp" && mv "${file}.tmp" "${file}"
}
_set_auto_sleep() {
    local file="${SRC_DIR}/config.yaml" start end
    start=$(grep -n "^supervisor:" "${file}" | head -1 | cut -d: -f1)
    end=$(awk -v s="${start}" 'NR>s && /^[^[:space:]]/ { print NR; exit }' "${file}")
    [ -z "${end}" ] && end=$(wc -l < "${file}")
    awk -v s="${start}" -v e="${end}" -v en="$1" -v a="$2" '
        NR>=s+1 && NR<e {
            if ($0 ~ /^    enabled:/) { print "    enabled: " en; next }
            if ($0 ~ /^    after_s:/) { print "    after_s: " a; next }
        } { print }' "${file}" > "${file}.tmp" && mv "${file}.tmp" "${file}"
}

# ----------------------------------------------------------------------------
# Step 0 — pre-flight
# ----------------------------------------------------------------------------
log "Step 0/10 — pre-flight checks"
command -v apt-get >/dev/null 2>&1 || die "apt-get not found — this installer targets Debian/Armbian/DietPi."
if [ -r /etc/os-release ]; then . /etc/os-release; info "OS: ${PRETTY_NAME:-unknown} (kernel $(uname -r))"; fi
[ -f "${AUDIO_DIR}/owl_i2s.c" ]            || die "missing ${AUDIO_DIR}/owl_i2s.c"
[ -f "${AUDIO_DIR}/owl-i2s-overlay.dts" ]  || die "missing ${AUDIO_DIR}/owl-i2s-overlay.dts"
[ -f "${AUDIO_DIR}/asound.conf" ]          || die "missing ${AUDIO_DIR}/asound.conf"
[ -f "${UNIT_SRC}" ]                        || die "missing ${UNIT_SRC}"
[ -f "${UDEV_SRC}" ]                        || die "missing ${UDEV_SRC}"
[ -d "/lib/modules/$(uname -r)/build" ] || warn "kernel build tree not present yet — step 1 installs linux-headers."

# ----------------------------------------------------------------------------
# Step 1 — system packages
# ----------------------------------------------------------------------------
log "Step 1/10 — installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3-venv python3-pip alsa-utils rsync portaudio19-dev \
    dtc bc make gcc curl
# Kernel headers are required to build owl_i2s against the running kernel.
apt-get install -y --no-install-recommends "linux-headers-$(uname -r)" \
    || warn "could not install linux-headers-$(uname -r) — the module build (step 2) will fail."
[ -d "/lib/modules/$(uname -r)/build" ] || die "no kernel build tree — cannot build owl_i2s. Install the matching linux-headers package."

# ----------------------------------------------------------------------------
# Step 2 — build + install the owl_i2s kernel module
# ----------------------------------------------------------------------------
log "Step 2/10 — building + installing the owl_i2s kernel module"
make -C "${AUDIO_DIR}" clean >/dev/null 2>&1 || true
make -C "${AUDIO_DIR}" KDIR="/lib/modules/$(uname -r)/build"
make -C "${AUDIO_DIR}" KDIR="/lib/modules/$(uname -r)/build" install
# Load the module on every boot. The DT overlay (step 3) muxes the I2S0 pins;
# the module provides the snd-soc card that asound.conf names 'owl'.
echo owl_i2s > "${MODULE_LOAD_CONF}"
depmod -a
info "Module installed under /lib/modules/$(uname -r)/extra/robot-owl (loaded at boot via ${MODULE_LOAD_CONF})."

# ----------------------------------------------------------------------------
# Step 3 — compile + install + enable the DT overlay
# ----------------------------------------------------------------------------
log "Step 3/10 — compiling + installing + enabling the ${OVERLAY_NAME} DT overlay"
# Find the boot directory holding the .dtb (DietPi/Armbian vary: /boot vs
# /boot/firmware).
BOOT_DIR=""
for cand in /boot/firmware /boot; do
    if ls "${cand}"/*.dtb >/dev/null 2>&1; then BOOT_DIR="${cand}"; break; fi
done
[ -n "${BOOT_DIR}" ] || die "could not locate a boot directory with a .dtb (checked /boot/firmware, /boot)."
info "Using boot directory: ${BOOT_DIR}"
command -v dtc >/dev/null 2>&1 || die "dtc not available (installed in step 1) — cannot compile the overlay."
dtc -I dts -O dtb -o "${AUDIO_DIR}/${OVERLAY_NAME}.dtbo" "${AUDIO_DIR}/owl-i2s-overlay.dts"
install -m 0644 "${AUDIO_DIR}/${OVERLAY_NAME}.dtbo" "${BOOT_DIR}/${OVERLAY_NAME}.dtbo"
# Enable the overlay at boot. Armbian/DietPi images differ in how overlays are
# selected, so use the common mechanism and WARN (not die) if it is absent —
# the fallback is to merge the overlay into the base .dtb (see specs/015 Open).
if [ -f "${BOOT_DIR}/armbianEnv.txt" ]; then
    grep -q "^dtoverlay=${OVERLAY_NAME}$" "${BOOT_DIR}/armbianEnv.txt" \
        || echo "dtoverlay=${OVERLAY_NAME}" >> "${BOOT_DIR}/armbianEnv.txt"
    info "Enabled via ${BOOT_DIR}/armbianEnv.txt (dtoverlay=${OVERLAY_NAME})."
else
    warn "No ${BOOT_DIR}/armbianEnv.txt — this DietPi image may enable overlays"
    warn "differently (U-Boot 'fdtoverlay' in extlinux.conf, or a per-board"
    warn "mechanism). The .dtbo is installed at ${BOOT_DIR}/${OVERLAY_NAME}.dtbo."
    warn "If the 'owl' ALSA card is absent after reboot, enable the overlay per"
    warn "your image's docs (see specs/015, Open) — e.g. merge it into the base .dtb."
fi

# ----------------------------------------------------------------------------
# Step 4 — install the ALSA mixer config
# ----------------------------------------------------------------------------
log "Step 4/10 — installing /etc/asound.conf (shared 48 kHz 'owl' card)"
install -m 0644 "${AUDIO_DIR}/asound.conf" /etc/asound.conf
info "asound.conf installed. The 'owl' card appears after the reboot in step 10."

# ----------------------------------------------------------------------------
# Step 5 — install the brain + create the virtualenv
# ----------------------------------------------------------------------------
log "Step 5/10 — installing the brain to ${DEST_DIR}"
mkdir -p "${DEST_DIR}"
rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'deploy' --exclude 'audio' --exclude 'models' \
    "${SRC_DIR}/" "${DEST_DIR}/"
if [ ! -x "${DEST_DIR}/.venv/bin/python" ]; then
    info "Creating virtualenv ..."
    python3 -m venv "${DEST_DIR}/.venv"
fi
"${DEST_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null
"${DEST_DIR}/.venv/bin/pip" install -r "${DEST_DIR}/requirements.txt"

# ----------------------------------------------------------------------------
# Step 6 — config wizard (writes choices into the installed config.yaml)
# ----------------------------------------------------------------------------
if [ "${INTERACTIVE}" -eq 1 ]; then
    run_config_wizard
else
    log "Step 6/10 — non-interactive: keeping bundled config.yaml defaults"
fi
# The wizard edited the SOURCE config.yaml; refresh the installed copy.
install -m 0644 "${SRC_DIR}/config.yaml" "${DEST_DIR}/config.yaml"

# ----------------------------------------------------------------------------
# Step 7 — pre-download the whisper.cpp model
# ----------------------------------------------------------------------------
log "Step 7/10 — whisper.cpp model"
SPEECH_ENABLED=$("${DEST_DIR}/.venv/bin/python" -c "import yaml;print(yaml.safe_load(open('${DEST_DIR}/config.yaml'))['speech']['enabled'])" 2>/dev/null || echo false)
if [ "${SPEECH_ENABLED}" = "True" ] || [ "${SPEECH_ENABLED}" = "true" ]; then
    MODEL_SIZE=$("${DEST_DIR}/.venv/bin/python" -c "import yaml;print(yaml.safe_load(open('${DEST_DIR}/config.yaml'))['speech']['model'])" 2>/dev/null || echo small)
    case "${MODEL_SIZE}" in
        tiny|base|small|medium) ;;
        *) warn "speech.model '${MODEL_SIZE}' is not a known size — skipping download."; MODEL_SIZE="" ;;
    esac
    if [ -n "${MODEL_SIZE}" ]; then
        MODELS_DIR="${DEST_DIR}/models/whisper"
        mkdir -p "${MODELS_DIR}"
        if [ -f "${MODELS_DIR}/ggml-${MODEL_SIZE}.bin" ]; then
            info "Model already present: ${MODELS_DIR}/ggml-${MODEL_SIZE}.bin"
        else
            info "Downloading ggml-${MODEL_SIZE}.bin (this can be a large download) ..."
            curl -fL --retry 3 -o "${MODELS_DIR}/ggml-${MODEL_SIZE}.bin" \
                "${WHISPER_MODEL_BASE}/ggml-${MODEL_SIZE}.bin"
        fi
        # Point config.yaml at the absolute installed path.
        awk -v p="${MODELS_DIR}/ggml-${MODEL_SIZE}.bin" '
            $0 ~ /^  model:/ { print "  model: \"" p "\""; next } { print }' \
            "${DEST_DIR}/config.yaml" > "${DEST_DIR}/config.yaml.tmp" \
            && mv "${DEST_DIR}/config.yaml.tmp" "${DEST_DIR}/config.yaml"
        info "speech.model -> ${MODELS_DIR}/ggml-${MODEL_SIZE}.bin"
        warn "Emotion GGUF is NOT auto-downloaded (the exact GGUF URL for the"
        warn "German emotion model is not pinned down). If you enable emotion,"
        warn "download the .gguf into ${DEST_DIR}/models/emotion/ and set"
        warn "emotion.model in config.yaml — the brain errors clearly if it is missing."
    fi
else
    info "Speech is disabled in config.yaml — no model download."
fi

# ----------------------------------------------------------------------------
# Step 8 — dedicated user + udev rule for the ESP32 serial port
# ----------------------------------------------------------------------------
log "Step 8/10 — creating '${SERVICE_USER}' user + udev rule"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --home "${DEST_DIR}" --shell /usr/sbin/nologin \
        --comment "Robot Owl brain" "${SERVICE_USER}"
    info "Created system user '${SERVICE_USER}'."
else
    info "User '${SERVICE_USER}' already exists."
fi
usermod -aG dial "${SERVICE_USER}" 2>/dev/null || true
install -m 0644 "${UDEV_SRC}" "${UDEV_DEST}"
chown root:root "${UDEV_DEST}"
# The brain runs as ${SERVICE_USER}; give it read access to the installed tree.
chown -R "${SERVICE_USER}:" "${DEST_DIR}"

# ----------------------------------------------------------------------------
# Step 9 — install + enable the systemd service
# ----------------------------------------------------------------------------
log "Step 9/10 — installing + enabling robot-owl-brain.service"
install -m 0644 "${UNIT_SRC}" "${UNIT_DEST}"
systemctl daemon-reload
systemctl enable robot-owl-brain.service
info "Service enabled — it starts on the next boot, once the sound card is up."

# ----------------------------------------------------------------------------
# Step 10 — reboot to apply the DT overlay
# ----------------------------------------------------------------------------
log "Step 10/10 — rebooting to apply the DT overlay (brings up the 'owl' card)"
info "The owl starts automatically after reboot (robot-owl-brain.service is enabled)."
info "Rebooting in 5 seconds ..."
sleep 5
exec reboot
