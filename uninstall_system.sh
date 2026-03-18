#!/usr/bin/env bash
# uninstall_system.sh — Fully removes the productivity monitor
# Must be run as root:  sudo bash uninstall_system.sh

set -e

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must be run as root.  Try: sudo bash uninstall_system.sh"
    exit 1
fi

echo "Removing productivity monitor..."

# Remove immutable flag before deleting
chattr -i /etc/systemd/system/productivity-monitor@.service   2>/dev/null || true
chattr -i /opt/productivity_monitor/productivity_monitor.py   2>/dev/null || true

# Stop and disable all instances
systemctl stop    'productivity-monitor@*.service' 2>/dev/null || true
systemctl disable 'productivity-monitor@*.service' 2>/dev/null || true

# Remove files
rm -rf /opt/productivity_monitor
rm -f  /etc/systemd/system/productivity-monitor@.service

systemctl daemon-reload

echo "Done. Logs are still at /var/log/productivity_monitor/ — remove manually if needed."
