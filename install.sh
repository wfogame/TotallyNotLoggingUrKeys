#!/usr/bin/env bash
# install.sh — Sets up the productivity monitor and enables autostart
#
# Run once per machine:
#   chmod +x install.sh
#   ./install.sh

set -e  # stop on any error

echo "======================================================"
echo "  Productivity Monitor — Installer"
echo "======================================================"

# ── 1. Install pynput if missing ───────────────────────────────────────────

echo ""
echo "[1/4] Checking Python dependency (pynput)..."
if ! python3 -c "import pynput" &>/dev/null; then
    echo "      Installing pynput..."
    pip install --user pynput
else
    echo "      pynput already installed. ✓"
fi

# ── 2. Copy monitor script to ~/productivity_monitor/ ─────────────────────

INSTALL_DIR="$HOME/productivity_monitor"
LOG_DIR="$INSTALL_DIR/logs"

echo ""
echo "[2/4] Installing monitor script to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$LOG_DIR"

# Copy the monitor script from the same folder as this installer
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$SCRIPT_DIR/productivity_monitor.py" ]; then
    echo "ERROR: productivity_monitor.py not found next to install.sh"
    echo "Make sure both files are in the same folder."
    exit 1
fi

cp "$SCRIPT_DIR/productivity_monitor.py" "$INSTALL_DIR/productivity_monitor.py"
echo "      Copied. ✓"

# ── 3. Install the systemd user service ───────────────────────────────────

SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/productivity-monitor.service"

echo ""
echo "[3/4] Installing systemd user service..."
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Employee Productivity Monitor
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $INSTALL_DIR/productivity_monitor.py --output $LOG_DIR/productivity_log.txt
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=XAUTHORITY=$HOME/.Xauthority

[Install]
WantedBy=default.target
EOF

echo "      Service file written to $SERVICE_FILE ✓"

# ── 4. Enable and start the service ───────────────────────────────────────

echo ""
echo "[4/4] Enabling autostart and starting service now..."

# Enable lingering so user services survive without an active session
# (optional but good practice on shared machines)
loginctl enable-linger "$USER" 2>/dev/null || true

systemctl --user daemon-reload
systemctl --user enable productivity-monitor.service
systemctl --user start  productivity-monitor.service

echo "      Service enabled and started. ✓"

# ── Done ───────────────────────────────────────────────────────────────────

echo ""
echo "======================================================"
echo "  Installation complete!"
echo ""
echo "  Monitor script : $INSTALL_DIR/productivity_monitor.py"
echo "  Log files      : $LOG_DIR/"
echo ""
echo "  Useful commands:"
echo "    Check status : systemctl --user status productivity-monitor"
echo "    View logs    : journalctl --user -u productivity-monitor -f"
echo "    Stop it      : systemctl --user stop productivity-monitor"
echo "    Disable      : systemctl --user disable productivity-monitor"
echo "======================================================"
