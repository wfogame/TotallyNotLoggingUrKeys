#!/usr/bin/env python3
"""
remote_server.py — Lightweight secure remote command server.
Runs on each employee machine. Accepts bash commands from an authorized manager.

Security:
  - All traffic is TLS encrypted (self-signed cert generated at install)
  - Manager must authenticate with a shared secret token
  - Every command and its output is logged with timestamp and source IP
  - Runs as root so it can execute any system command

Requirements:
    pip install cryptography

Usage (handled by installer):
    python3 remote_server.py --token <secret> --cert cert.pem --key key.pem
"""

import argparse
import hashlib
import hmac
import json
import logging
import os
import shlex
import socket
import ssl
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

REMOTE_LOG  = "/var/log/productivity_monitor/remote_commands.log"
DEFAULT_PORT = 7722   # not 22, avoids confusion with real SSH


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    Path(REMOTE_LOG).parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("remote_server")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  REMOTE  %(levelname)s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(REMOTE_LOG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ── Protocol helpers ──────────────────────────────────────────────────────────
# Messages are newline-delimited JSON over TLS.

def send_msg(conn: ssl.SSLSocket, data: dict):
    raw = (json.dumps(data) + "\n").encode()
    conn.sendall(raw)

def recv_msg(conn: ssl.SSLSocket) -> dict | None:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
    return json.loads(buf.split(b"\n")[0])


# ── Client handler ────────────────────────────────────────────────────────────

class ClientHandler(threading.Thread):
    def __init__(self, conn: ssl.SSLSocket, addr, token: str, logger: logging.Logger):
        super().__init__(daemon=True)
        self.conn   = conn
        self.addr   = addr
        self.token  = token
        self.logger = logger

    def run(self):
        ip = self.addr[0]
        try:
            # ── Auth handshake ────────────────────────────────────────────────
            msg = recv_msg(self.conn)
            if not msg or msg.get("type") != "auth":
                send_msg(self.conn, {"type": "auth_fail", "reason": "expected auth"})
                return

            provided = msg.get("token", "")
            # Constant-time compare to prevent timing attacks
            if not hmac.compare_digest(
                hashlib.sha256(provided.encode()).hexdigest(),
                hashlib.sha256(self.token.encode()).hexdigest()
            ):
                send_msg(self.conn, {"type": "auth_fail", "reason": "bad token"})
                self.logger.warning(f"Auth FAILED from {ip}")
                return

            send_msg(self.conn, {"type": "auth_ok", "hostname": socket.gethostname()})
            self.logger.info(f"Manager connected from {ip}")

            # ── Command loop ──────────────────────────────────────────────────
            while True:
                msg = recv_msg(self.conn)
                if not msg:
                    break

                if msg.get("type") == "disconnect":
                    self.logger.info(f"Manager disconnected from {ip}")
                    break

                if msg.get("type") == "command":
                    cmd = msg.get("cmd", "").strip()
                    if not cmd:
                        continue

                    self.logger.info(f"CMD from {ip}: {cmd}")

                    try:
                        result = subprocess.run(
                            cmd,
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                        send_msg(self.conn, {
                            "type":        "result",
                            "stdout":      result.stdout,
                            "stderr":      result.stderr,
                            "returncode":  result.returncode,
                        })
                        self.logger.info(
                            f"CMD result: exit={result.returncode}  "
                            f"stdout={result.stdout[:80].strip()!r}"
                        )
                    except subprocess.TimeoutExpired:
                        send_msg(self.conn, {
                            "type":   "result",
                            "stdout": "",
                            "stderr": "Command timed out (30s limit).",
                            "returncode": -1,
                        })

        except Exception as e:
            self.logger.error(f"Error handling {ip}: {e}")
        finally:
            self.conn.close()


# ── Server ────────────────────────────────────────────────────────────────────

class RemoteServer:
    def __init__(self, host: str, port: int, token: str,
                 certfile: str, keyfile: str, logger: logging.Logger):
        self.host     = host
        self.port     = port
        self.token    = token
        self.certfile = certfile
        self.keyfile  = keyfile
        self.logger   = logger

    def run(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.certfile, self.keyfile)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.listen(5)
            self.logger.info(
                f"Remote server listening on {self.host}:{self.port}"
            )

            with ctx.wrap_socket(sock, server_side=True) as tls_sock:
                while True:
                    conn, addr = tls_sock.accept()
                    ClientHandler(conn, addr, self.token, self.logger).start()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Remote command server.")
    parser.add_argument("--token",    required=True, help="Shared secret auth token")
    parser.add_argument("--cert",     required=True, help="TLS certificate file (PEM)")
    parser.add_argument("--key",      required=True, help="TLS private key file (PEM)")
    parser.add_argument("--host",     default="0.0.0.0")
    parser.add_argument("--port",     type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    logger = setup_logging()
    server = RemoteServer(args.host, args.port, args.token,
                          args.cert, args.key, logger)
    server.run()


if __name__ == "__main__":
    main()
