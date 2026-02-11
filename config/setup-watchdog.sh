#!/usr/bin/env bash
# setup-watchdog.sh — Enable hardware watchdog on Raspberry Pi 5
# Run as root: sudo bash setup-watchdog.sh
#
# The Pi 5 has a built-in BCM2712 hardware watchdog (bcm2835_wdt).
# If the system hangs completely (kernel panic, full deadlock), the
# hardware watchdog reboots the Pi automatically.
#
# Combined with the systemd service watchdog (WatchdogSec=30) which
# restarts the *process*, this gives two layers of resilience:
#   1. Process hang  → systemd kills & restarts the service
#   2. System hang   → hardware watchdog reboots the Pi

set -euo pipefail

echo "=== Pi Hardware Watchdog Setup ==="

# 1. Install watchdog daemon
if ! command -v watchdog &>/dev/null; then
    echo "Installing watchdog package..."
    apt-get update -qq && apt-get install -y watchdog
else
    echo "watchdog package already installed"
fi

# 2. Enable hardware watchdog in config.txt (dtparam=watchdog=on)
BOOT_CONFIG="/boot/firmware/config.txt"
if ! grep -q "dtparam=watchdog=on" "$BOOT_CONFIG" 2>/dev/null; then
    echo "Enabling hardware watchdog in $BOOT_CONFIG..."
    echo "" >> "$BOOT_CONFIG"
    echo "# Hardware watchdog" >> "$BOOT_CONFIG"
    echo "dtparam=watchdog=on" >> "$BOOT_CONFIG"
else
    echo "Hardware watchdog already enabled in $BOOT_CONFIG"
fi

# 3. Configure /etc/watchdog.conf
WATCHDOG_CONF="/etc/watchdog.conf"
echo "Configuring $WATCHDOG_CONF..."
cat > "$WATCHDOG_CONF" << 'WDCONF'
# Hardware watchdog device
watchdog-device = /dev/watchdog
# Timeout in seconds — reboot if not pinged within this window
watchdog-timeout = 15
# Max allowed system load (4 cores)
max-load-1 = 24
# Realtime scheduling priority
realtime = yes
priority = 1
WDCONF

# 4. Enable and start watchdog service
echo "Enabling watchdog service..."
systemctl enable watchdog
systemctl restart watchdog

echo ""
echo "=== Done ==="
echo "Hardware watchdog is now active."
echo "The Pi will auto-reboot if the system becomes unresponsive."
echo ""
echo "A reboot is recommended to ensure dtparam=watchdog=on takes effect."
echo "Run: sudo reboot"
