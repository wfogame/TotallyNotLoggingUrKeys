#!/usr/bin/env python3
"""
tamper_watchdog.py — Monitors the productivity monitor for tampering.

Detects:
  - Script or service file being edited (hash change)
  - Script or service file being deleted
  - Productivity monitor service being stopped or disabled
  - This watchdog itself being killed (logs a shutdown event before exit)

Alerts are sent to:
  - A central syslog server (configured at install time)
  - /var/log/productivity_monitor/tamper.log (local backup)

Run as a system service (see install_system.sh).
"""

import hashlib
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# ── Paths being watched ───────────────────────────────────────────────────────

WATCHED_FILES = [
    "/opt/productivity_monitor/productivity_monitor.py",
    "/etc/systemd/system/productivity-monitor@.service",
    "/opt/productivity_monitor/tamper_watchdog.py",
]

MONITOR_SERVICE_PATTERN = "productivity-monitor@"   # check any instance
LOCAL_TAMPER_LOG        = "/var/log/productivity_monitor/tamper.log"
CHECK_INTERVAL          = 30   # seconds between checks


# ── Helpers ───────────────────────────────────────────────────────────────────

def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def file_hash(path: str) -> str | None:
    """SHA-256 of a file, or None if it doesn't exist."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except (FileNotFoundError, PermissionError):
        return None

def service_is_running() -> bool:
    """Returns True if at least one productivity-monitor instance is active."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", f"{MONITOR_SERVICE_PATTERN}*"],
            capture_output=True
        )
        # Also try listing units explicitly
        list_result = subprocess.run(
            ["systemctl", "list-units", "--state=running",
             f"{MONITOR_SERVICE_PATTERN}*", "--no-legend"],
            capture_output=True, text=True
        )
        return bool(list_result.stdout.strip())
    except Exception:
        return False


# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging(syslog_host: str, syslog_port: int) -> logging.Logger:
    logger = logging.getLogger("tamper_watchdog")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s  TAMPER_WATCHDOG  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. Local file handler (always)
    Path(LOCAL_TAMPER_LOG).parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOCAL_TAMPER_LOG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # 2. Remote syslog handler
    try:
        sh = logging.handlers.SysLogHandler(address=(syslog_host, syslog_port))
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        logger.info(f"Watchdog started — remote logging to {syslog_host}:{syslog_port}")
    except Exception as e:
        logger.warning(f"Could not connect to syslog server {syslog_host}:{syslog_port} — {e}")
        logger.warning("Falling back to local log only.")

    # 3. Console (useful when running interactively / debugging)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# ── Watchdog ──────────────────────────────────────────────────────────────────

class TamperWatchdog:
    def __init__(self, logger: logging.Logger):
        self.logger       = logger
        self._running     = True
        self._hashes      = {}   # path -> last known hash
        self._svc_was_up  = True

        # Snapshot current state at startup
        for path in WATCHED_FILES:
            h = file_hash(path)
            self._hashes[path] = h
            if h is None:
                self.logger.warning(f"MISSING at startup: {path}")
            else:
                self.logger.info(f"Baseline hash for {path}: {h[:12]}...")

        self._svc_was_up = service_is_running()
        self.logger.info(f"Monitor service running at startup: {self._svc_was_up}")

        # Catch SIGTERM so we log before dying
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGINT,  self._handle_sigterm)

    def _handle_sigterm(self, signum, frame):
        self.logger.critical(
            "WATCHDOG TERMINATED — signal received. "
            "Someone stopped the tamper watchdog service. Investigate immediately."
        )
        self._running = False
        sys.exit(0)

    def _alert(self, message: str):
        """Log a tamper alert at CRITICAL level so it stands out."""
        full = f"⚠️  TAMPER ALERT — {message}"
        self.logger.critical(full)

    def check(self):
        # 1. Check file hashes
        for path in WATCHED_FILES:
            current_hash = file_hash(path)
            last_hash    = self._hashes[path]

            if last_hash is not None and current_hash is None:
                self._alert(f"FILE DELETED: {path}")

            elif last_hash is None and current_hash is not None:
                self._alert(f"FILE RESTORED (was missing): {path}  new hash: {current_hash[:12]}...")
                self._hashes[path] = current_hash

            elif last_hash is not None and current_hash != last_hash:
                self._alert(
                    f"FILE MODIFIED: {path}  "
                    f"old={last_hash[:12]}...  new={current_hash[:12]}..."
                )
                self._hashes[path] = current_hash  # update so we don't spam

        # 2. Check service is still running
        svc_now_up = service_is_running()
        if self._svc_was_up and not svc_now_up:
            self._alert(
                "MONITOR SERVICE STOPPED — productivity-monitor is no longer running. "
                "An administrator may have stopped or disabled it."
            )
        elif not self._svc_was_up and svc_now_up:
            self.logger.info("Monitor service came back up.")
        self._svc_was_up = svc_now_up

    def run(self):
        self.logger.info(f"Watchdog running. Checking every {CHECK_INTERVAL}s.")
        while self._running:
            try:
                self.check()
            except Exception as e:
                self.logger.error(f"Error during check: {e}")
            time.sleep(CHECK_INTERVAL)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Productivity monitor tamper watchdog.")
    parser.add_argument("--syslog-host", required=True,
                        help="Hostname or IP of the central syslog server")
    parser.add_argument("--syslog-port", type=int, default=514,
                        help="UDP port of the syslog server (default: 514)")
    args = parser.parse_args()

    logger = setup_logging(args.syslog_host, args.syslog_port)
    watchdog = TamperWatchdog(logger)
    watchdog.run()


if __name__ == "__main__":
    main()
