#!/usr/bin/env python3
"""
productivity_monitor.py — Enhanced Employee Productivity Monitor
Tracks keystrokes, mouse activity, active/idle time, AND per-window/app activity.

Requirements:
    pip install pynput python-xlib

Usage:
    python3 productivity_monitor.py --output /var/log/productivity_monitor/alice.txt
    python3 productivity_monitor.py --idle-timeout 120
"""

import argparse
import math
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    from pynput import keyboard, mouse
except ImportError:
    print("Run:  pip install pynput")
    sys.exit(1)

try:
    from Xlib import display as xdisplay, X
    XLIB_AVAILABLE = True
except ImportError:
    XLIB_AVAILABLE = False


DEFAULT_LOG_FILE  = "/var/log/productivity_monitor/activity.txt"
DEFAULT_IDLE_SECS = 60
STATUS_INTERVAL   = 5
WINDOW_POLL_SECS  = 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem  = divmod(seconds, 3600)
    m, s    = divmod(rem, 60)
    if h:   return f"{h}h {m}m {s}s"
    if m:   return f"{m}m {s}s"
    return f"{s}s"

def distance(p1, p2) -> float:
    return math.sqrt((p2[0]-p1[0])**2 + (p2[1]-p1[1])**2)


# ── Active window detection ───────────────────────────────────────────────────

def get_active_window_title() -> str:
    if not XLIB_AVAILABLE:
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=1
            )
            return result.stdout.strip() or "Unknown"
        except Exception:
            return "Unknown"
    try:
        d    = xdisplay.Display()
        root = d.screen().root
        NET_ACTIVE_WINDOW = d.intern_atom("_NET_ACTIVE_WINDOW")
        NET_WM_NAME       = d.intern_atom("_NET_WM_NAME")
        WM_NAME           = d.intern_atom("WM_NAME")
        active = root.get_full_property(NET_ACTIVE_WINDOW, X.AnyPropertyType)
        if not active or not active.value:
            return "Unknown"
        win = d.create_resource_object("window", active.value[0])
        for atom in (NET_WM_NAME, WM_NAME):
            prop = win.get_full_property(atom, X.AnyPropertyType)
            if prop and prop.value:
                title = prop.value
                if isinstance(title, bytes):
                    title = title.decode("utf-8", errors="replace")
                d.close()
                return title.strip() or "Unknown"
        d.close()
    except Exception:
        pass
    return "Unknown"


# ── Monitor ───────────────────────────────────────────────────────────────────

class ProductivityMonitor:
    def __init__(self, log_path: Path | None, idle_timeout: int):
        self.log_path     = log_path
        self.idle_timeout = idle_timeout

        self.key_count    = 0
        self.click_count  = 0
        self.scroll_count = 0
        self.mouse_dist   = 0.0
        self._last_pos    = None

        self.session_start = time.time()
        self._last_active  = time.time()
        self.idle_total    = 0.0
        self._idle_start   = None
        self.is_idle       = False

        self.window_time: dict[str, float]  = defaultdict(float)
        self._current_window                = "Unknown"
        self._window_since                  = time.time()
        self.window_input: dict[str, dict]  = defaultdict(
            lambda: {"keys": 0, "clicks": 0, "scrolls": 0}
        )

        self._file = None
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(log_path, "a", encoding="utf-8")
            self._write(f"\n{'='*70}")
            self._write(f"Session started : {timestamp()}")
            self._write(f"Idle threshold  : {idle_timeout}s")
            self._write(f"{'='*70}\n")

    def _write(self, line: str):
        if self._file:
            self._file.write(line + "\n")
            self._file.flush()

    def _mark_active(self):
        now = time.time()
        if self.is_idle:
            idle_dur = now - self._idle_start
            self.idle_total += idle_dur
            self._log_event("IDLE_END", f"Idle for {fmt_duration(idle_dur)}")
            self.is_idle     = False
            self._idle_start = None
        self._last_active = now

    def _log_event(self, kind: str, detail: str):
        entry = {"time": timestamp(), "kind": kind, "detail": detail}
        self._write(f"[{entry['time']}]  {kind:<14}  {detail}")

    def _update_window_time(self):
        now = time.time()
        self.window_time[self._current_window] += now - self._window_since
        self._window_since = now

    def on_key_press(self, key):
        self._mark_active()
        self.key_count += 1
        self.window_input[self._current_window]["keys"] += 1
        if key == keyboard.Key.esc:
            return False

    def on_key_release(self, key): pass

    def on_mouse_move(self, x, y):
        self._mark_active()
        pos = (x, y)
        if self._last_pos:
            self.mouse_dist += distance(self._last_pos, pos)
        self._last_pos = pos

    def on_mouse_click(self, x, y, button, pressed):
        if pressed:
            self._mark_active()
            self.click_count += 1
            self.window_input[self._current_window]["clicks"] += 1

    def on_mouse_scroll(self, x, y, dx, dy):
        self._mark_active()
        self.scroll_count += 1
        self.window_input[self._current_window]["scrolls"] += 1

    def _idle_watcher(self):
        while not self._stop.is_set():
            time.sleep(1)
            now = time.time()
            if not self.is_idle and (now - self._last_active) >= self.idle_timeout:
                self.is_idle     = True
                self._idle_start = self._last_active
                self._log_event("IDLE_START", f"No input for {self.idle_timeout}s")

    def _window_watcher(self):
        while not self._stop.is_set():
            time.sleep(WINDOW_POLL_SECS)
            title = get_active_window_title()
            if title != self._current_window:
                self._update_window_time()
                self._log_event("WINDOW", f"{self._current_window!r} → {title!r}")
                self._current_window = title

    def _status_printer(self):
        while not self._stop.is_set():
            time.sleep(STATUS_INTERVAL)
            now     = time.time()
            elapsed = now - self.session_start
            idle    = self.idle_total + (now - self._idle_start if self.is_idle else 0)
            active  = elapsed - idle
            status  = "💤 IDLE" if self.is_idle else "✅ ACTIVE"
            print(
                f"\r  [{timestamp()}]  {status}  |  "
                f"Active: {fmt_duration(active)}  Idle: {fmt_duration(idle)}  |  "
                f"Keys: {self.key_count}  Clicks: {self.click_count}  "
                f"Scrolls: {self.scroll_count}  "
                f"Mouse: {self.mouse_dist/1000:.1f}k px  |  "
                f"Window: {self._current_window[:40]!r}",
                end="", flush=True
            )

    def start(self):
        self._stop = threading.Event()
        threading.Thread(target=self._idle_watcher,   daemon=True).start()
        threading.Thread(target=self._window_watcher, daemon=True).start()
        threading.Thread(target=self._status_printer, daemon=True).start()

        self._kb = keyboard.Listener(on_press=self.on_key_press,
                                     on_release=self.on_key_release)
        self._ms = mouse.Listener(on_move=self.on_mouse_move,
                                  on_click=self.on_mouse_click,
                                  on_scroll=self.on_mouse_scroll)
        self._kb.start()
        self._ms.start()
        self._kb.join()
        self.stop()

    def stop(self):
        self._stop.set()
        self._update_window_time()
        try: self._ms.stop()
        except: pass
        self._finalize()

    def _finalize(self):
        now     = time.time()
        elapsed = now - self.session_start
        idle    = self.idle_total + (now - self._idle_start if self.is_idle else 0)
        active  = elapsed - idle

        window_report = sorted(self.window_time.items(), key=lambda x: x[1], reverse=True)

        lines = [
            "",
            "=" * 70,
            f"Session ended      : {timestamp()}",
            f"Total duration     : {fmt_duration(elapsed)}",
            f"Active time        : {fmt_duration(active)}  ({100*active/max(elapsed,1):.1f}%)",
            f"Idle time          : {fmt_duration(idle)}  ({100*idle/max(elapsed,1):.1f}%)",
            "-" * 70,
            f"Keystrokes         : {self.key_count}",
            f"Mouse clicks       : {self.click_count}",
            f"Mouse scrolls      : {self.scroll_count}",
            f"Mouse distance     : {self.mouse_dist/1000:.1f}k pixels",
            "-" * 70,
            "Per-Window Activity (sorted by time spent):",
            "",
        ]

        for title, secs in window_report:
            pct    = 100 * secs / max(elapsed, 1)
            inp    = self.window_input.get(title, {})
            keys   = inp.get("keys", 0)
            clicks = inp.get("clicks", 0)
            bar    = "█" * int(pct / 2)
            lines.append(
                f"  {fmt_duration(secs):>10}  ({pct:5.1f}%)  {bar:<25}  "
                f"keys={keys:<5} clicks={clicks:<4}  {title[:50]}"
            )

        lines += ["", "=" * 70]

        print("\n")
        for line in lines:
            print(line)
            self._write(line)

        if self._file:
            self._file.close()
        if self.log_path:
            print(f"\nLog saved to: {self.log_path.resolve()}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Enhanced productivity monitor.")
    parser.add_argument("--output", "-o", default=DEFAULT_LOG_FILE)
    parser.add_argument("--no-file", action="store_true")
    parser.add_argument("--idle-timeout", "-i", type=int, default=DEFAULT_IDLE_SECS)
    args = parser.parse_args()

    log_path = None if args.no_file else Path(args.output)
    monitor  = ProductivityMonitor(log_path=log_path, idle_timeout=args.idle_timeout)

    print("┌─────────────────────────────────────────────────────────────┐")
    print("│         Enhanced Productivity Monitor  —  Running          │")
    print("│  Tracking: keys · mouse · time · per-window activity       │")
    print("│  Stop: Esc or Ctrl+C                                       │")
    print("└─────────────────────────────────────────────────────────────┘\n")

    def handle_interrupt(sig, frame):
        print("\n\nStopping...")
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_interrupt)
    monitor.start()


if __name__ == "__main__":
    main()
