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
#   7. Pre-download the speech model (Vosk, or whisper.cpp with engine: whisper)
#      so the first transcription is instant instead of a long fetch. The mood
#      model is built on a dev machine (tools/train_mood.py) and copied over.
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
#   skips the wizard and preserves an existing installed config (or uses defaults).
#   Add --no-reboot --no-enable-service to stage without USB/servo startup.
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../orangepi-brain
DEST_DIR="/opt/robot-owl/orangepi-brain"
AUDIO_DIR="${SRC_DIR}/audio"
UNIT_SRC="${SRC_DIR}/deploy/robot-owl-brain.service"
UDEV_SRC="${SRC_DIR}/deploy/99-robot-owl-serial.rules"
UDEV_DEST="/etc/udev/rules.d/99-robot-owl-serial.rules"
UNIT_DEST="/etc/systemd/system/robot-owl-brain.service"
POWER_UNIT_SRC="${SRC_DIR}/deploy/robot-owl-power.service"
POWER_UNIT_DEST="/etc/systemd/system/robot-owl-power.service"
HOTSPOT_UNIT_SRC="${SRC_DIR}/deploy/robot-owl-hotspot.service"
HOTSPOT_UNIT_DEST="/etc/systemd/system/robot-owl-hotspot.service"
HOTSPOT_PSK="/etc/robot-owl/hotspot.psk"
GPIO_RULE_SRC="${SRC_DIR}/deploy/99-robot-owl-gpio.rules"
GPIO_RULE_DEST="/etc/udev/rules.d/99-robot-owl-gpio.rules"
MODULE_LOAD_CONF="/etc/modules-load.d/robot-owl-i2s.conf"
SERVICE_USER="robotowl"
OVERLAY_NAME="owl-i2s-overlay"
WHISPER_MODEL_BASE="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
VOSK_MODEL_NAME="vosk-model-small-de-0.15"
VOSK_MODEL_URL="https://alphacephei.com/vosk/models/${VOSK_MODEL_NAME}.zip"

log()  { printf '\n\033[1;32m[robot-owl]\033[0m %s\n' "$*"; }
info() { printf '\033[0;37m[robot-owl]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[robot-owl] WARN:\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[robot-owl] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo:  sudo ./setup.sh"

# ----------------------------------------------------------------------------
# Options: --non-interactive skips the config wizard (keeps bundled defaults).
# ----------------------------------------------------------------------------
NON_INTERACTIVE=0
NO_REBOOT=0
NO_ENABLE_SERVICE=0
for arg in "$@"; do
    case "${arg}" in
        --non-interactive|-n) NON_INTERACTIVE=1 ;;
        --no-reboot) NO_REBOOT=1 ;;
        --no-enable-service) NO_ENABLE_SERVICE=1 ;;
        *) die "unknown argument '${arg}' (--non-interactive, --no-reboot, --no-enable-service)" ;;
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
    first=$(ls /dev/ttyACM* 2>/dev/null | head -1) || true
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
    local speech_enabled="false" speech_model="tiny" speech_lang="de"
    if [ "${INTERACTIVE}" -eq 1 ]; then
        ask "Enable speech recognition (I2S mic -> Vosk)? [false]" speech_enabled
        ask "Whisper.cpp model, only used with speech.engine: whisper (tiny/base/small) [tiny]" speech_model
        ask "Spoken language (ISO code) [de]" speech_lang
    fi
    [ -z "${speech_model}" ] && speech_model="tiny"
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
[ -f "${POWER_UNIT_SRC}" ]                  || die "missing ${POWER_UNIT_SRC}"
[ -f "${GPIO_RULE_SRC}" ]                   || die "missing ${GPIO_RULE_SRC}"
[ -d "/lib/modules/$(uname -r)/build" ] || warn "kernel build tree not present yet — step 1 installs linux-headers."

# ----------------------------------------------------------------------------
# Step 1 — system packages
# ----------------------------------------------------------------------------
log "Step 1/10 — installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3-venv python3-pip alsa-utils rsync portaudio19-dev \
    device-tree-compiler bc make gcc g++ cmake ninja-build pkg-config python3-dev curl \
    libasound2-plugins hostapd dnsmasq-base
# The fallback hotspot (brain/hotspot.py) starts hostapd and dnsmasq itself, only when
# no known WLAN is reachable. dnsmasq-base has no service of its own; hostapd's must not
# grab wlan0 at boot, so it stays masked (Debian ships it masked; make sure).
systemctl mask hostapd.service >/dev/null 2>&1 || true
# DietPi's A733 headers are a versioned vendor package, not linux-headers-<uname>.
# Match the installed image version: never silently upgrade the kernel here.
if [ ! -d "/lib/modules/$(uname -r)/build" ]; then
    if dpkg-query -W -f='${Version}' linux-image-vendor-sun60iw2 >/dev/null 2>&1; then
        KERNEL_PACKAGE_VERSION=$(dpkg-query -W -f='${Version}' linux-image-vendor-sun60iw2)
        apt-get install -y --no-install-recommends "linux-headers-vendor-sun60iw2=${KERNEL_PACKAGE_VERSION}"
    else
        apt-get install -y --no-install-recommends "linux-headers-$(uname -r)"
    fi
fi
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
    if [ -f "${cand}/dietpiEnv.txt" ] || [ -f "${cand}/armbianEnv.txt" ]; then
        BOOT_DIR="${cand}"; break
    fi
done
[ -n "${BOOT_DIR}" ] || die "could not locate dietpiEnv.txt or armbianEnv.txt under /boot/firmware or /boot."
info "Using boot directory: ${BOOT_DIR}"
command -v dtc >/dev/null 2>&1 || die "dtc not available (installed in step 1) — cannot compile the overlay."
dtc -I dts -O dtb -o "${AUDIO_DIR}/${OVERLAY_NAME}.dtbo" "${AUDIO_DIR}/owl-i2s-overlay.dts"
install -d "${BOOT_DIR}/overlay-user"
install -m 0644 "${AUDIO_DIR}/${OVERLAY_NAME}.dtbo" "${BOOT_DIR}/overlay-user/${OVERLAY_NAME}.dtbo"
# Enable the overlay at boot. Armbian/DietPi images differ in how overlays are
# selected, so use the common mechanism and WARN (not die) if it is absent —
# the fallback is to merge the overlay into the base .dtb (see specs/015 Open).
ENV_FILE="${BOOT_DIR}/dietpiEnv.txt"
[ -f "${ENV_FILE}" ] || ENV_FILE="${BOOT_DIR}/armbianEnv.txt"
[ -f "${ENV_FILE}.robot-owl-backup" ] || cp -a "${ENV_FILE}" "${ENV_FILE}.robot-owl-backup"
python3 - "${ENV_FILE}" "${OVERLAY_NAME}" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
lines = path.read_text().splitlines()
values = []
for line in lines:
    if line.startswith('user_overlays='):
        values.extend(line.split('=', 1)[1].split())
values = list(dict.fromkeys(values + [sys.argv[2]]))
lines = [line for line in lines if not line.startswith('user_overlays=')]
lines.append('user_overlays=' + ' '.join(values))
path.write_text('\n'.join(lines) + '\n')
PY
info "Enabled via ${ENV_FILE} (user_overlays=${OVERLAY_NAME}); reboot still required."

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
    --exclude 'deploy' --exclude 'audio' --exclude 'models' --exclude 'config.yaml' \
    "${SRC_DIR}/" "${DEST_DIR}/"
if [ ! -x "${DEST_DIR}/.venv/bin/python" ]; then
    info "Creating virtualenv ..."
    python3 -m venv "${DEST_DIR}/.venv"
fi
"${DEST_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null
CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-2}" \
    "${DEST_DIR}/.venv/bin/pip" install -r "${DEST_DIR}/requirements.txt"

# ----------------------------------------------------------------------------
# Step 6 — config wizard (writes choices into the installed config.yaml)
# ----------------------------------------------------------------------------
if [ "${INTERACTIVE}" -eq 1 ]; then
    run_config_wizard
else
    log "Step 6/10 — non-interactive: preserving installed config (defaults only on first install)"
fi
# Preserve deployment choices on unattended re-runs; the wizard explicitly
# requests new choices. Never overwrite an existing deployment with defaults.
if [ "${INTERACTIVE}" -eq 1 ] || [ ! -f "${DEST_DIR}/config.yaml" ]; then
    install -m 0644 "${SRC_DIR}/config.yaml" "${DEST_DIR}/config.yaml"
fi

# ----------------------------------------------------------------------------
# Step 7 — pre-download the speech model (Vosk by default, whisper.cpp optional)
# ----------------------------------------------------------------------------
log "Step 7/10 — speech model"
_cfg() { "${DEST_DIR}/.venv/bin/python" -c "import yaml;c=yaml.safe_load(open('${DEST_DIR}/config.yaml'));print($1)" 2>/dev/null; }
_set_key() {  # _set_key <indented key> <value>: replace one config.yaml line in place
    awk -v k="$1" -v v="$2" '$0 ~ "^"k":" { print k": \"" v "\""; next } { print }' \
        "${DEST_DIR}/config.yaml" > "${DEST_DIR}/config.yaml.tmp" && mv "${DEST_DIR}/config.yaml.tmp" "${DEST_DIR}/config.yaml"
}
SPEECH_ENABLED=$(_cfg "c['speech']['enabled']" || echo false)
if [ "${SPEECH_ENABLED}" = "True" ] || [ "${SPEECH_ENABLED}" = "true" ]; then
    ENGINE=$(_cfg "c['speech'].get('engine','whisper')" || echo vosk)
    if [ "${ENGINE}" = "vosk" ]; then
        VOSK_DIR="${DEST_DIR}/models/vosk"
        mkdir -p "${VOSK_DIR}"
        if [ -d "${VOSK_DIR}/${VOSK_MODEL_NAME}" ]; then
            info "Vosk model already present: ${VOSK_DIR}/${VOSK_MODEL_NAME}"
        else
            info "Downloading ${VOSK_MODEL_NAME} (~45 MB) ..."
            curl -fL --retry 3 -o "${VOSK_DIR}/model.zip.part" "${VOSK_MODEL_URL}"
            # python's zipfile, not unzip: DietPi does not ship unzip.
            "${DEST_DIR}/.venv/bin/python" -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
                "${VOSK_DIR}/model.zip.part" "${VOSK_DIR}"
            rm -f "${VOSK_DIR}/model.zip.part"
        fi
        _set_key "  vosk_model" "${VOSK_DIR}/${VOSK_MODEL_NAME}"
        info "speech.vosk_model -> ${VOSK_DIR}/${VOSK_MODEL_NAME}"
    else
        MODEL_SIZE=$(_cfg "c['speech']['model']" || echo tiny)
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
                curl -fL --retry 3 -o "${MODELS_DIR}/ggml-${MODEL_SIZE}.bin.part" \
                    "${WHISPER_MODEL_BASE}/ggml-${MODEL_SIZE}.bin"
                mv "${MODELS_DIR}/ggml-${MODEL_SIZE}.bin.part" "${MODELS_DIR}/ggml-${MODEL_SIZE}.bin"
            fi
            _set_key "  model" "${MODELS_DIR}/ggml-${MODEL_SIZE}.bin"
            info "speech.model -> ${MODELS_DIR}/ggml-${MODEL_SIZE}.bin"
        fi
    fi
    if [ ! -f "${DEST_DIR}/models/mood/model.onnx" ]; then
        warn "No mood model in ${DEST_DIR}/models/mood/. It is not downloadable: build it on a"
        warn "dev machine with 'python3 tools/train_mood.py' and copy build/mood/* there, then"
        warn "set mood.enabled: true and mood.model_dir: ${DEST_DIR}/models/mood in config.yaml."
    fi
else
    info "Speech is disabled in config.yaml — no model download."
fi

# ----------------------------------------------------------------------------
# Step 8 — dedicated user + udev rule for the ESP32 serial port
# ----------------------------------------------------------------------------
log "Step 8/10 — creating '${SERVICE_USER}' user + udev rule"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --user-group --home "${DEST_DIR}" --shell /usr/sbin/nologin \
        --comment "Robot Owl brain" "${SERVICE_USER}"
    info "Created system user '${SERVICE_USER}'."
else
    info "User '${SERVICE_USER}' already exists."
fi
# i2c: the status OLED (brain/display.py) opens /dev/i2c-N, which belongs to group i2c.
# The service unit names the group, so it must exist.
getent group i2c >/dev/null || groupadd --system i2c
# gpio: the mute switch (brain/mute_switch.py) reads /dev/gpiochip*, root-only by default.
getent group gpio >/dev/null || groupadd --system gpio
usermod -aG dialout,audio,i2c,gpio "${SERVICE_USER}"
install -m 0644 "${UDEV_SRC}" "${UDEV_DEST}"
chown root:root "${UDEV_DEST}"
install -m 0644 "${GPIO_RULE_SRC}" "${GPIO_RULE_DEST}"
# The brain runs as ${SERVICE_USER}; give it read access to the installed tree.
chown -R "${SERVICE_USER}:" "${DEST_DIR}"

# ----------------------------------------------------------------------------
# Step 9 — install + enable the systemd service
# ----------------------------------------------------------------------------
log "Step 9/10 — installing + enabling robot-owl-brain.service"
install -m 0644 "${UNIT_SRC}" "${UNIT_DEST}"
install -m 0644 "${POWER_UNIT_SRC}" "${POWER_UNIT_DEST}"
install -m 0644 "${HOTSPOT_UNIT_SRC}" "${HOTSPOT_UNIT_DEST}"
systemctl daemon-reload
# Like the power button, the hotspot touches no USB and no servo: enabled always, it
# does nothing until hotspot.enabled is true. Its passphrase never goes into the repo.
systemctl enable robot-owl-hotspot.service
install -d -m 0700 /etc/robot-owl
if [ ! -s "${HOTSPOT_PSK}" ]; then
    warn "No hotspot passphrase yet. To use the fallback hotspot, write one (8..63 chars):"
    warn "  sudo sh -c 'umask 077; printf %s \"<passphrase>\" > ${HOTSPOT_PSK}'"
fi
# The power button touches no USB and no servo, so it is enabled even with
# --no-enable-service; it does nothing until power_button.enabled is true.
systemctl enable robot-owl-power.service
if [ "${NO_ENABLE_SERVICE}" -eq 0 ]; then
    systemctl enable robot-owl-brain.service
    info "Service enabled — starts on next boot (opening USB may reset the XIAO)."
else
    info "Service not enabled by this run (--no-enable-service)."
fi

# ----------------------------------------------------------------------------
# Step 10 — reboot to apply the DT overlay
# ----------------------------------------------------------------------------
if [ "${NO_REBOOT}" -eq 1 ]; then
    log "Installed without reboot. Audio activation is pending."
    warn "Do not reboot until XIAO/servo startup is mechanically safe."
    exit 0
fi
log "Step 10/10 — rebooting to apply the DT overlay (brings up the 'owl' card)"
info "The owl starts automatically after reboot (robot-owl-brain.service is enabled)."
info "Rebooting in 5 seconds ..."
sleep 5
exec reboot
