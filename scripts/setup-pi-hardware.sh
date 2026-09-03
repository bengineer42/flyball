#!/usr/bin/env bash
#
# Configure a fresh Raspberry Pi OS install for humctrl hardware access:
#   - enable the I2C bus on the GPIO header      (SHT4x humidity/temperature sensor)
#   - enable the hardware PWM channels           (pump drive)
#   - grant the target user non-root access to both
#
# Idempotent: safe to re-run. Makes a timestamped backup before touching config.txt.
# Does NOT reboot; it tells you when one is required.
#
# Usage:  sudo ./setup-pi-hardware.sh [username]
#         sudo ./setup-pi-hardware.sh --verify        # check only, change nothing
#
set -euo pipefail

# The default pwm-2chan overlay maps pwm0 -> GPIO18 (pin 12) and pwm1 -> GPIO19 (pin 35),
# matching the rpi_hardware_pwm docstring. For GPIO12/13 instead, use:
#   dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
PWM_OVERLAY="dtoverlay=pwm-2chan"
UDEV_RULE_PATH="/etc/udev/rules.d/99-pwm.rules"
MARKER="# added by humctrl setup-pi-hardware.sh"

REBOOT_REQUIRED=0
VERIFY_ONLY=0

# ---------------------------------------------------------------- output helpers

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()    { printf '  \033[1;32mok\033[0m   %s\n' "$*"; }
warn()  { printf '  \033[1;33mwarn\033[0m %s\n' "$*" >&2; }
skip()  { printf '  \033[1;90mskip\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- argument parsing

TARGET_USER=""
for arg in "$@"; do
    case "$arg" in
        --verify) VERIFY_ONLY=1 ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        -*) die "unknown option: $arg" ;;
        *)  TARGET_USER="$arg" ;;
    esac
done

# Fall back to the invoking user, since $USER is root under sudo.
TARGET_USER="${TARGET_USER:-${SUDO_USER:-pi}}"

# ---------------------------------------------------------------- preflight

if [[ $VERIFY_ONLY -eq 0 && $EUID -ne 0 ]]; then
    die "must run as root: sudo $0 ${TARGET_USER}"
fi

id "$TARGET_USER" >/dev/null 2>&1 || die "no such user: ${TARGET_USER}"

info "Target user: ${TARGET_USER}"

# ---------------------------------------------------------------- verification

verify() {
    local failures=0

    info "Verifying"

    if [[ -d /sys/class/pwm/pwmchip0 ]]; then
        ok "/sys/class/pwm/pwmchip0 exists"
        local export_grp export_mode
        export_grp=$(stat -c '%G' /sys/class/pwm/pwmchip0/export)
        export_mode=$(stat -c '%a' /sys/class/pwm/pwmchip0/export)
        if [[ "$export_grp" == "gpio" ]]; then
            ok "export is group '${export_grp}', mode ${export_mode}"
        else
            warn "export is group '${export_grp}' (expected gpio), mode ${export_mode}"
            failures=$((failures + 1))
        fi
    else
        warn "/sys/class/pwm/pwmchip0 missing - PWM overlay not active (reboot needed?)"
        failures=$((failures + 1))
    fi

    # Note: /dev/i2c-2 is the HDMI DDC bus and is present even with the GPIO
    # header bus disabled. Only /dev/i2c-1 corresponds to pins 3 and 5.
    if [[ -e /dev/i2c-1 ]]; then
        ok "/dev/i2c-1 exists, group $(stat -c '%G' /dev/i2c-1), mode $(stat -c '%a' /dev/i2c-1)"

        if command -v i2cdetect >/dev/null 2>&1; then
            local found=""
            for addr in 44 45; do
                if i2cdetect -y 1 2>/dev/null | grep -qiE "(^| )${addr}( |$)"; then
                    found="0x${addr}"
                    break
                fi
            done
            if [[ -n "$found" ]]; then
                ok "SHT4x responding at ${found}"
            else
                warn "no SHT4x found at 0x44/0x45 - check wiring:"
                warn "  SDA -> pin 3 (GPIO2), SCL -> pin 5 (GPIO3), 3V3 -> pin 1, GND -> pin 6"
                warn "  the SHT4x is a 3.3V part - do not power it from pin 2/4 (5V)"
                failures=$((failures + 1))
            fi
        else
            skip "i2cdetect not available, cannot probe for the sensor"
        fi
    else
        warn "/dev/i2c-1 missing - I2C not enabled (reboot needed?)"
        failures=$((failures + 1))
    fi

    local groups_now
    groups_now=$(id -nG "$TARGET_USER")
    for grp in gpio i2c; do
        if [[ " $groups_now " == *" $grp "* ]]; then
            ok "${TARGET_USER} is in group '${grp}'"
        else
            warn "${TARGET_USER} is NOT in group '${grp}'"
            failures=$((failures + 1))
        fi
    done

    if [[ $failures -eq 0 ]]; then
        info "All checks passed."
        return 0
    fi
    warn "${failures} check(s) failed."
    warn "If you have not rebooted since running this script, do that first."
    return 1
}

# --verify inspects runtime state only, so it needs neither root nor a boot config.
if [[ $VERIFY_ONLY -eq 1 ]]; then
    verify
    exit 0
fi

# ---------------------------------------------------------------- boot config

# Bookworm and later moved the boot partition to /boot/firmware.
CONFIG_TXT=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "$candidate" ]]; then CONFIG_TXT="$candidate"; break; fi
done
[[ -n "$CONFIG_TXT" ]] || die "no config.txt found - is this a Raspberry Pi?"
info "Boot config: ${CONFIG_TXT}"

# ---------------------------------------------------------------- backup

BACKUP="${CONFIG_TXT}.humctrl-$(date +%Y%m%d-%H%M%S).bak"
cp -a "$CONFIG_TXT" "$BACKUP"
ok "backed up ${CONFIG_TXT} -> ${BACKUP}"

# Append a setting to config.txt only if it is not already active (uncommented).
ensure_config_line() {
    local line="$1" description="$2"
    # Match the directive at the start of a line, ignoring leading whitespace.
    local key="${line%%=*}"
    if grep -qE "^[[:space:]]*${key}=" "$CONFIG_TXT"; then
        local existing
        existing=$(grep -E "^[[:space:]]*${key}=" "$CONFIG_TXT" | head -1)
        if [[ "$(echo "$existing" | tr -d '[:space:]')" == "$(echo "$line" | tr -d '[:space:]')" ]]; then
            skip "${description}: already set (${existing})"
            return 0
        fi
        warn "${description}: found a different value: ${existing}"
        warn "  leaving it alone - edit ${CONFIG_TXT} by hand if that is wrong"
        return 0
    fi
    printf '\n%s\n%s\n' "$MARKER" "$line" >> "$CONFIG_TXT"
    ok "${description}: added '${line}'"
    REBOOT_REQUIRED=1
}

# ---------------------------------------------------------------- packages

info "Installing packages"
# i2c-tools provides i2cdetect, used by the verification step below.
# swig and python3-dev are build dependencies for lgpio, which pip builds from
# source on Python versions piwheels has no prebuilt wheel for.
APT_PACKAGES=(i2c-tools swig python3-dev)
MISSING_PACKAGES=()
for pkg in "${APT_PACKAGES[@]}"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "^install ok installed$"; then
        skip "${pkg} already installed"
    else
        MISSING_PACKAGES+=("$pkg")
    fi
done

if [[ ${#MISSING_PACKAGES[@]} -gt 0 ]]; then
    info "Installing: ${MISSING_PACKAGES[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING_PACKAGES[@]}"
    ok "installed ${MISSING_PACKAGES[*]}"
fi

# ---------------------------------------------------------------- I2C

info "Enabling I2C"
if [[ -e /dev/i2c-1 ]]; then
    skip "I2C already enabled (/dev/i2c-1 present)"
elif command -v raspi-config >/dev/null 2>&1; then
    # do_i2c 0 means "enable" - it also handles /etc/modules and modprobe blacklists.
    raspi-config nonint do_i2c 0
    ok "raspi-config nonint do_i2c 0"
    REBOOT_REQUIRED=1
else
    warn "raspi-config not found, falling back to editing config.txt directly"
    ensure_config_line "dtparam=i2c_arm=on" "I2C on GPIO header"
fi

# ---------------------------------------------------------------- PWM overlay

info "Enabling hardware PWM"
ensure_config_line "$PWM_OVERLAY" "hardware PWM overlay"

# ---------------------------------------------------------------- groups

info "Configuring groups"
for grp in gpio i2c spi; do
    if ! getent group "$grp" >/dev/null; then
        groupadd -f "$grp"
        ok "created group '${grp}'"
    fi
    if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx "$grp"; then
        skip "${TARGET_USER} already in '${grp}'"
    else
        usermod -aG "$grp" "$TARGET_USER"
        ok "added ${TARGET_USER} to '${grp}'"
        REBOOT_REQUIRED=1   # group membership only applies to new login sessions
    fi
done

# ---------------------------------------------------------------- udev rule

info "Installing udev rule"

# /sys/class/pwm/* entries are symlinks into /sys/devices/..., so both paths need
# fixing up - chmod on the symlink does not touch the underlying inode.
#
# SUBSYSTEM=="pwm*" fires again when a channel is exported, because the new pwmN/
# directory raises its own uevent. That is what grants access to period, duty_cycle
# and enable, which do not exist until export time.
#
# The /sys/devices glob covers both Pi 4 (.../platform/soc/...) and Pi 5, whose PWM
# sits behind the RP1 southbridge at a different path.
read -r -d '' UDEV_RULE <<'EOF' || true
# Managed by humctrl setup-pi-hardware.sh - hardware PWM access for the gpio group.
SUBSYSTEM=="pwm*", PROGRAM="/bin/sh -c '\
	chown -R root:gpio /sys/class/pwm && chmod -R 770 /sys/class/pwm;\
	chown -R root:gpio /sys/devices/platform/*/*.pwm/pwm/pwmchip* && chmod -R 770 /sys/devices/platform/*/*.pwm/pwm/pwmchip*\
'"
EOF

if [[ -f "$UDEV_RULE_PATH" ]] && printf '%s\n' "$UDEV_RULE" | diff -q - "$UDEV_RULE_PATH" >/dev/null 2>&1; then
    skip "${UDEV_RULE_PATH} already up to date"
else
    printf '%s\n' "$UDEV_RULE" > "$UDEV_RULE_PATH"
    chmod 644 "$UDEV_RULE_PATH"
    ok "wrote ${UDEV_RULE_PATH}"
fi

udevadm control --reload-rules
ok "udevadm control --reload-rules"

# Only meaningful if the pwm subsystem already exists; harmless otherwise.
udevadm trigger --subsystem-match=pwm || warn "udevadm trigger failed (expected before first reboot)"

# ---------------------------------------------------------------- summary

echo
# Failing checks here are expected before the reboot, so do not abort.
verify || true
echo

if [[ $REBOOT_REQUIRED -eq 1 ]]; then
    info "Reboot required to apply device-tree and group changes:"
    printf '\n    sudo reboot\n\n'
    info "After rebooting, re-check with:  ${0} --verify"
else
    info "No reboot required."
fi
