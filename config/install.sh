#!/usr/bin/env bash
# install.sh — Full Pi Medienserver installation
# Run as root: sudo bash config/install.sh
#
# This script:
#   1. Installs the systemd service (auto-restart + watchdog)
#   2. Configures silent boot (no text, no logo, black screen)
#   3. Disables unnecessary services for faster boot
#   4. Optionally installs the hardware watchdog
#
# Recovery: If something goes wrong, boot files are backed up to *.bak

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CMDLINE="/boot/firmware/cmdline.txt"
CONFIG="/boot/firmware/config.txt"

echo "=== Pi Medienserver Installation ==="
echo ""

# Must run as root
if [[ $EUID -ne 0 ]]; then
    echo "Error: Run this script with sudo"
    exit 1
fi

# ----- 1. Install systemd service -----
echo "[1/4] Installing systemd service..."
cp "$SCRIPT_DIR/mediaserver.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable mediaserver
echo "      mediaserver.service enabled"

# ----- 2. Configure silent boot -----
echo "[2/4] Configuring silent boot..."

# Backup boot files
cp -n "$CMDLINE" "$CMDLINE.bak" 2>/dev/null || true
cp -n "$CONFIG" "$CONFIG.bak" 2>/dev/null || true

# Read current cmdline
CURRENT_CMDLINE=$(cat "$CMDLINE")

# Add quiet boot parameters if not present
PARAMS_TO_ADD=""
for param in "quiet" "loglevel=0" "logo.nologo" "vt.global_cursor_default=0" "systemd.show_status=0"; do
    if [[ ! "$CURRENT_CMDLINE" =~ $param ]]; then
        PARAMS_TO_ADD="$PARAMS_TO_ADD $param"
    fi
done

if [[ -n "$PARAMS_TO_ADD" ]]; then
    echo "$CURRENT_CMDLINE$PARAMS_TO_ADD" > "$CMDLINE"
    echo "      Added boot params:$PARAMS_TO_ADD"
fi

# Redirect console from tty1 (visible) to tty3 (hidden)
if grep -q "console=tty1" "$CMDLINE"; then
    sed -i 's/console=tty1/console=tty3/' "$CMDLINE"
    echo "      Redirected console to tty3 (hidden)"
fi

# Disable rainbow splash in config.txt
if ! grep -q "disable_splash=1" "$CONFIG"; then
    echo "" >> "$CONFIG"
    echo "# Silent boot" >> "$CONFIG"
    echo "disable_splash=1" >> "$CONFIG"
    echo "      Added disable_splash=1"
fi

# ----- 3. Disable unnecessary services -----
echo "[3/4] Disabling unnecessary services..."

SERVICES_TO_DISABLE=(
    "NetworkManager-wait-online.service"
    "cloud-init-main.service"
    "cloud-init-network.service"
    "cloud-init-local.service"
    "cloud-init.target"
    "cloud-config.service"
    "cloud-final.service"
    "bluetooth.service"
    "ModemManager.service"
)

for svc in "${SERVICES_TO_DISABLE[@]}"; do
    if systemctl is-enabled "$svc" &>/dev/null; then
        systemctl disable "$svc" &>/dev/null || true
        echo "      Disabled $svc"
    fi
done

# Hide login prompt on tty1 (optional, keeps serial console for recovery)
systemctl mask getty@tty1.service &>/dev/null || true
echo "      Masked getty@tty1"

# ----- 4. Hardware watchdog (optional) -----
echo "[4/4] Hardware watchdog..."
read -p "      Install hardware watchdog? (reboots Pi on system hang) [y/N]: " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    bash "$SCRIPT_DIR/setup-watchdog.sh"
else
    echo "      Skipped (run config/setup-watchdog.sh later if needed)"
fi

# ----- Done -----
echo ""
echo "=== Installation Complete ==="
echo ""
echo "Services:"
echo "  - mediaserver.service: enabled (auto-starts at boot)"
echo "  - Watchdog: $(systemctl is-active watchdog 2>/dev/null || echo 'not installed')"
echo ""
echo "Boot config:"
echo "  - Silent boot: enabled"
echo "  - Console: redirected to tty3"
echo ""
echo "Backups created:"
echo "  - $CMDLINE.bak"
echo "  - $CONFIG.bak"
echo ""
echo "Reboot to apply all changes:"
echo "  sudo reboot"
