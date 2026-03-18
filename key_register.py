"""
key_register.py — Keystroke logger with timestamps
Logs every key pressed to the console and to a log file.

Requirements:
    pip install pynput

Usage:
    python key_register.py
    python key_register.py --output my_log.txt
    python key_register.py --no-file   (console only, no file saved)

Stop recording: press Ctrl+C  or  Esc
"""

import argparse
import signal
import sys
from datetime import datetime
from pathlib import Path

try:
    from pynput import keyboard
except ImportError:
    print("Missing dependency. Install it with:\n\n    pip install pynput\n")
    sys.exit(1)


# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_LOG_FILE = "key_log.txt"


# ── Helpers ───────────────────────────────────────────────────────────────────

def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # ms precision


def format_key(key) -> str:
    """Return a readable string for any key."""
    try:
        # Regular character key (a, b, 1, !, etc.)
        return repr(key.char)
    except AttributeError:
        # Special key (Shift, Enter, F1, etc.)
        name = str(key).replace("Key.", "")
        return f"[{name}]"


# ── Core ──────────────────────────────────────────────────────────────────────

class KeyRegister:
    def __init__(self, log_path: Path | None):
        self.log_path = log_path
        self.count = 0
        self._file = None

        if log_path:
            self._file = open(log_path, "a", encoding="utf-8")
            self._file.write(f"\n{'='*60}\n")
            self._file.write(f"Session started: {timestamp()}\n")
            self._file.write(f"{'='*60}\n")
            self._file.flush()

    def on_press(self, key):
        ts = timestamp()
        key_str = format_key(key)
        self.count += 1

        line = f"[{ts}]  {key_str}"
        print(line)

        if self._file:
            self._file.write(line + "\n")
            self._file.flush()

        # Stop on Esc
        if key == keyboard.Key.esc:
            return False  # signals pynput to stop the listener

    def on_release(self, key):
        pass  # not logging releases, but hook is here if you want it

    def close(self):
        if self._file:
            self._file.write(f"\nSession ended:   {timestamp()}\n")
            self._file.write(f"Total keys logged: {self.count}\n")
            self._file.flush()
            self._file.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Log keystrokes with timestamps.")
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_LOG_FILE,
        help=f"File to write log to (default: {DEFAULT_LOG_FILE})",
    )
    parser.add_argument(
        "--no-file",
        action="store_true",
        help="Print to console only — don't write a file",
    )
    args = parser.parse_args()

    log_path = None if args.no_file else Path(args.output)

    register = KeyRegister(log_path)

    print("┌─────────────────────────────────────────┐")
    print("│         Key Register  —  Running        │")
    print("│  Press  Esc  or  Ctrl+C  to stop        │")
    if log_path:
        print(f"│  Logging to: {str(log_path):<27}│")
    else:
        print("│  Console only (no file)                 │")
    print("└─────────────────────────────────────────┘\n")

    # Graceful Ctrl+C shutdown
    def handle_interrupt(sig, frame):
        print("\n\nInterrupted.")
        register.close()
        if log_path:
            print(f"Log saved to: {log_path.resolve()}")
        print(f"Total keys logged: {register.count}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_interrupt)

    with keyboard.Listener(
        on_press=register.on_press,
        on_release=register.on_release,
    ) as listener:
        listener.join()  # blocks until listener returns False (Esc pressed)

    register.close()
    print(f"\nStopped.  Total keys logged: {register.count}")
    if log_path:
        print(f"Log saved to: {log_path.resolve()}")


if __name__ == "__main__":
    main()
