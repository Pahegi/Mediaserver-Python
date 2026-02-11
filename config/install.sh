#!/usr/bin/env bash
# install.sh — Full Pi Medienserver installation
# Run as root: sudo bash config/install.sh
#
# This script:
#   1. Installs the systemd service (auto-restart + watchdog)
#   2. Configures silent boot (no text, no logo, black screen)
#   3. Disables unnecessary services for faster boot
#   4. Sets up WiFi management permissions
#   5. Optionally installs the hardware watchdog
#   6. Optionally installs NDI SDK for NDI stream support
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
echo "[1/6] Installing systemd service..."
cp "$SCRIPT_DIR/mediaserver.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable mediaserver
echo "      mediaserver.service enabled"

# ----- 2. Configure silent boot -----
echo "[2/6] Configuring silent boot..."

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
echo "[3/6] Disabling unnecessary services..."

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

# ----- 4. WiFi management permissions -----
echo "[4/6] Setting up WiFi management permissions..."
POLKIT_RULE="/etc/polkit-1/rules.d/50-allow-pi-network.rules"
if [[ ! -f "$POLKIT_RULE" ]]; then
    cat > "$POLKIT_RULE" << 'EOFPK'
// Allow user 'pi' to manage NetworkManager without authentication
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 &&
        subject.user === "pi") {
        return polkit.Result.YES;
    }
});
EOFPK
    systemctl restart polkit &>/dev/null || true
    echo "      Created polkit rule for WiFi management"
else
    echo "      Polkit rule already exists"
fi

# ----- 5. Hardware watchdog (optional) -----
echo "[5/6] Hardware watchdog..."
read -p "      Install hardware watchdog? (reboots Pi on system hang) [y/N]: " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    bash "$SCRIPT_DIR/setup-watchdog.sh"
else
    echo "      Skipped (run config/setup-watchdog.sh later if needed)"
fi

# ----- 6. NDI SDK (optional) -----
echo "[6/6] NDI SDK for NDI stream support..."
NDI_INSTALLER="$SCRIPT_DIR/../Install_NDI_SDK_v6_Linux.sh"
NDI_LIB_SRC="/usr/local/NDI SDK for Linux/lib/aarch64-rpi4-linux-gnueabi"
NDI_LIB_DEST="/usr/local/lib"

if [[ -f "$NDI_LIB_DEST/libndi.so.6" ]]; then
    echo "      NDI SDK already installed"
elif [[ ! -f "$NDI_INSTALLER" ]]; then
    echo "      NDI SDK installer not found."
    echo "      To add NDI support later:"
    echo "        1. Download 'Install_NDI_SDK_v6_Linux.sh' from https://ndi.video/download-ndi-sdk/"
    echo "        2. Place it in the project root directory"
    echo "        3. Re-run this installer, or run manually:"
    echo "           sudo bash Install_NDI_SDK_v6_Linux.sh"
    echo "           sudo cp '/usr/local/NDI SDK for Linux/lib/aarch64-rpi4-linux-gnueabi/libndi.so.6' /usr/local/lib/"
    echo "           sudo ldconfig"
else
    read -p "      Install NDI SDK? (enables NDI stream playback) [y/N]: " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "      Running NDI SDK installer (requires accepting license)..."
        # Run NDI installer (it prompts for license acceptance)
        bash "$NDI_INSTALLER"
        
        # Copy library to system path
        if [[ -d "$NDI_LIB_SRC" ]]; then
            cp "$NDI_LIB_SRC/libndi.so.6" "$NDI_LIB_DEST/"
            # Create symlink for compatibility
            ln -sf "$NDI_LIB_DEST/libndi.so.6" "$NDI_LIB_DEST/libndi.so"
            ldconfig
            echo "      NDI SDK installed successfully"
        else
            echo "      Warning: NDI library not found at expected path"
            echo "      You may need to manually copy libndi.so.6 to /usr/local/lib/"
        fi
    else
        echo "      Skipped (can install NDI SDK later)"
    fi
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
echo "Optional features:"
if [[ -f "/usr/local/lib/libndi.so.6" ]]; then
    echo "  - NDI SDK: installed (NDI streams supported)"
else
    echo "  - NDI SDK: not installed (NDI streams disabled)"
fi
echo ""
echo "Backups created:"
echo "  - $CMDLINE.bak"
echo "  - $CONFIG.bak"
echo ""
echo "Reboot to apply all changes:"
echo "  sudo reboot"
