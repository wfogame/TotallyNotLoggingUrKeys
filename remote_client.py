#!/usr/bin/env python3
"""
remote_client.py — Manager-side CLI to connect to an employee machine.

Usage:
    python3 remote_client.py --host 192.168.1.42 --token mysecret
    python3 remote_client.py --host 192.168.1.42 --token mysecret --port 7722

Once connected you get an interactive prompt. Type any bash command.

Built-in shortcuts:
    report          — pull a formatted productivity report for today
    report <user>   — report for a specific user
    status          — show monitor service status
    logwatch        — tail the tamper log live
    exit / quit     — disconnect

Requirements:
    pip install cryptography   (for TLS — same as server side)
"""

import argparse
import json
import readline   # enables arrow keys / history in the prompt
import socket
import ssl
import sys
from datetime import datetime

DEFAULT_PORT = 7722


# ── Protocol helpers ──────────────────────────────────────────────────────────

def send_msg(conn: ssl.SSLSocket, data: dict):
    raw = (json.dumps(data) + "\n").encode()
    conn.sendall(raw)

def recv_msg(conn: ssl.SSLSocket) -> dict | None:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            return None
        buf += chunk
    return json.loads(buf.split(b"\n")[0])


# ── Built-in shortcut expansions ──────────────────────────────────────────────

def expand_shortcut(cmd: str) -> str:
    parts = cmd.strip().split()

    if parts[0] == "report":
        user = parts[1] if len(parts) > 1 else "$(logname || whoami)"
        return (
            f"echo '=== Productivity Report ===' && "
            f"cat /var/log/productivity_monitor/{user}.txt 2>/dev/null || "
            f"echo 'No log found for user: {user}'"
        )

    if parts[0] == "status":
        return "systemctl status 'productivity-monitor@*' --no-pager"

    if parts[0] == "logwatch":
        return "tail -f /var/log/productivity_monitor/tamper.log"

    return cmd   # pass through as raw bash


# ── Client ────────────────────────────────────────────────────────────────────

def connect(host: str, port: int, token: str) -> ssl.SSLSocket:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # We use a self-signed cert so we disable hostname/cert verification.
    # For production, replace with a proper CA-signed cert and enable verification.
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE

    raw = socket.create_connection((host, port), timeout=10)
    return ctx.wrap_socket(raw, server_hostname=host)


def run_session(host: str, port: int, token: str):
    print(f"Connecting to {host}:{port} ...")

    try:
        conn = connect(host, port, token)
    except (ConnectionRefusedError, socket.timeout) as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    # ── Authenticate ──────────────────────────────────────────────────────────
    send_msg(conn, {"type": "auth", "token": token})
    resp = recv_msg(conn)

    if not resp or resp.get("type") != "auth_ok":
        reason = resp.get("reason", "unknown") if resp else "no response"
        print(f"Authentication failed: {reason}")
        conn.close()
        sys.exit(1)

    hostname = resp.get("hostname", host)
    print(f"Connected to {hostname}  ({host}:{port})")
    print("Type any bash command. Shortcuts: report, status, logwatch, exit\n")

    # ── Interactive loop ──────────────────────────────────────────────────────
    try:
        while True:
            try:
                raw_input = input(f"[{hostname}] $ ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nDisconnecting...")
                break

            if not raw_input:
                continue

            if raw_input.lower() in ("exit", "quit", "q"):
                send_msg(conn, {"type": "disconnect"})
                break

            cmd = expand_shortcut(raw_input)
            send_msg(conn, {"type": "command", "cmd": cmd})

            resp = recv_msg(conn)
            if not resp:
                print("Connection closed by remote.")
                break

            stdout = resp.get("stdout", "").rstrip()
            stderr = resp.get("stderr", "").rstrip()
            rc     = resp.get("returncode", 0)

            if stdout:
                print(stdout)
            if stderr:
                print(f"\033[33m{stderr}\033[0m")   # yellow for stderr
            if rc != 0:
                print(f"\033[31m[exit code {rc}]\033[0m")

    finally:
        conn.close()
        print("Disconnected.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Connect to an employee machine and run commands."
    )
    parser.add_argument("--host",  required=True, help="Employee machine IP or hostname")
    parser.add_argument("--token", required=True, help="Shared secret (must match server)")
    parser.add_argument("--port",  type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    run_session(args.host, args.port, args.token)


if __name__ == "__main__":
    main()
