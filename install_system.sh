#!/usr/bin/env bash
# install_system.sh — Installs productivity monitor + tamper watchdog
#
# Run as root:  sudo bash install_system.sh
#
# You will be prompted for the central syslog server address.

set -e

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Must be run as root.  Try: sudo bash install_system.sh"
    exit 1
fi

echo "======================================================"
echo "  Productivity Monitor — Full System Installer"
echo "======================================================"

# ── Ask for syslog server details ─────────────────────────────────────────────

echo ""
echo "Central log server setup"
echo "------------------------"
read -rp "  Syslog server hostname or IP (e.g. 192.168.1.50): " SYSLOG_HOST
read -rp "  Syslog server port [514]: " SYSLOG_PORT
SYSLOG_PORT="${SYSLOG_PORT:-514}"

echo ""
echo "  Alerts will be sent to: $SYSLOG_HOST:$SYSLOG_PORT"
echo ""

# ── 1. Install dependencies ───────────────────────────────────────────────────

echo "[1/6] Installing Python dependencies..."
pip3 install pynput
echo "      Done. ✓"

# ── 2. Copy scripts to /opt ───────────────────────────────────────────────────

INSTALL_DIR="/opt/productivity_monitor"
LOG_DIR="/var/log/productivity_monitor"

echo ""
echo "[2/6] Installing scripts to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$LOG_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for f in productivity_monitor.py tamper_watchdog.py; do
    if [ ! -f "$SCRIPT_DIR/$f" ]; then
        echo "ERROR: $f not found next to install_system.sh"
        exit 1
    fi
    cp "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
done

chown -R root:root "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"
chown -R root:root "$LOG_DIR"
chmod 700 "$LOG_DIR"
echo "      Files secured. ✓"

# ── 3. Write the productivity monitor service ─────────────────────────────────

echo ""
echo "[3/6] Writing productivity monitor service..."

cat > /etc/systemd/system/productivity-monitor@.service << EOF
[Unit]
Description=Productivity Monitor for %i
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
User=%i
ExecStart=/usr/bin/python3 $INSTALL_DIR/productivity_monitor.py \
    --output $LOG_DIR/%i.txt
Restart=always
RestartSec=10
Environment=DISPLAY=:0
EnvironmentFile=-/run/user/%U/environment
ProtectSystem=strict
ReadWritePaths=$LOG_DIR

[Install]
WantedBy=graphical.target
EOF

chmod 644 /etc/systemd/system/productivity-monitor@.service
echo "      Done. ✓"

# ── 4. Write the tamper watchdog service ─────────────────────────────────────

echo ""
echo "[4/6] Writing tamper watchdog service..."

cat > /etc/systemd/system/tamper-watchdog.service << EOF
[Unit]
Description=Productivity Monitor Tamper Watchdog
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 $INSTALL_DIR/tamper_watchdog.py \
    --syslog-host $SYSLOG_HOST \
    --syslog-port $SYSLOG_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

chmod 644 /etc/systemd/system/tamper-watchdog.service
echo "      Done. ✓"

# ── 5. Enable all services ────────────────────────────────────────────────────

echo ""
echo "[5/6] Enabling services..."

systemctl daemon-reload

systemctl enable tamper-watchdog.service
systemctl start  tamper-watchdog.service

while IFS=: read -r username _ uid _ _ _ shell; do
    if [ "$uid" -ge 1000 ] && [ "$shell" != "/usr/sbin/nologin" ] && [ "$shell" != "/bin/false" ]; then
        echo "      Enabling monitor for: $username"
        systemctl enable "productivity-monitor@${username}.service" 2>/dev/null || true
        systemctl start  "productivity-monitor@${username}.service" 2>/dev/null || true
    fi
done < /etc/passwd

echo "      Done. ✓"

# ── 6. Lock down files with chattr ───────────────────────────────────────────

echo ""
echo "[6/6] Locking files against tampering (chattr +i)..."

chattr +i "$INSTALL_DIR/productivity_monitor.py"
chattr +i "$INSTALL_DIR/tamper_watchdog.py"
chattr +i /etc/systemd/system/productivity-monitor@.service
chattr +i /etc/systemd/system/tamper-watchdog.service

echo "      Files marked immutable. ✓"
echo "      To update later: chattr -i <file>  then re-run installer."

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "======================================================"
echo "  Installation complete!"
echo ""
echo "  Scripts         : $INSTALL_DIR/"
echo "  Logs            : $LOG_DIR/"
echo "  Tamper log      : $LOG_DIR/tamper.log"
echo "  Syslog server   : $SYSLOG_HOST:$SYSLOG_PORT"
echo ""
echo "  Admin commands:"
echo "    Watchdog status : systemctl status tamper-watchdog"
echo "    Tamper log      : cat $LOG_DIR/tamper.log"
echo "    Monitor status  : systemctl status 'productivity-monitor@*'"
echo "    Full removal    : sudo bash uninstall_system.sh"
echo "======================================================"
