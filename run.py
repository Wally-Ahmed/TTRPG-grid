#!/usr/bin/env python3
"""Entry point for the TTRPG Grid Map Generator.

Starts the Flask app bound to 127.0.0.1 (loopback only — the app makes zero
network calls at runtime and is not intended to be exposed to a network).

The default port is 8420. Port 5000 (the old Flask default) is avoided because
on modern macOS the AirPlay Receiver permanently occupies it, which silently
breaks the server. When the default port is busy the app automatically picks
the next free port; an explicitly requested port that is busy is a hard error.

Usage::

    python run.py                 # serve on http://127.0.0.1:8420
    PORT=8080 python run.py       # custom port
    python run.py --port 8080     # custom port (flag)
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from server.app import create_app

DEFAULT_PORT = 8420
# How far to scan upward from the default port when it is busy.
PORT_SCAN_SPAN = 20


def _port_is_free(host: str, port: int) -> bool:
    """Return True if (host, port) can be bound right now.

    Uses a throwaway socket with SO_REUSEADDR so this probe mirrors how Flask's
    development server binds, avoiding false negatives from lingering sockets.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _resolve_port(host: str, requested_port: int, explicit: bool) -> int:
    """Return a bindable port, or exit with a clear message.

    If the requested port is free, return it. If it is busy:
      * when it was explicitly requested (--port or $PORT), exit with an error;
      * when it is the default, scan upward to the first free port and announce
        the substitution.
    """
    if _port_is_free(host, requested_port):
        return requested_port

    if explicit:
        print(
            f"ERROR: port {requested_port} on {host} is already in use "
            f"(another program — possibly a previous instance of this app — is "
            f"holding it). Stop that process or pass --port <n> to pick another.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    for port in range(requested_port + 1, requested_port + 1 + PORT_SCAN_SPAN):
        if _port_is_free(host, port):
            print(f"Port {requested_port} busy — using {port} instead.")
            return port

    print(
        f"ERROR: no free port found in {requested_port}–"
        f"{requested_port + PORT_SCAN_SPAN} on {host}. "
        f"Free one up or pass --port <n>.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _open_browser_when_up(url: str, timeout: float = 10.0) -> None:
    """Poll the server locally and open a browser tab only once it responds.

    Runs in a daemon thread so it never blocks or outlives the server. The
    request targets 127.0.0.1 only — this is a loopback health check, not an
    external network call.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0):
                break
        except urllib.error.URLError:
            time.sleep(0.2)
        except OSError:
            time.sleep(0.2)
    else:
        # Server never came up within the timeout; skip opening a stale tab.
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="TTRPG Grid Map Generator server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host to bind (default 127.0.0.1 — loopback only)",
    )
    env_port = os.environ.get("PORT")
    parser.add_argument(
        "--port",
        type=int,
        default=int(env_port) if env_port is not None else DEFAULT_PORT,
        help=f"port to bind (default {DEFAULT_PORT}, or $PORT)",
    )
    parser.add_argument(
        "--library-root",
        default=os.environ.get("LIBRARY_ROOT", "library-data"),
        help="directory for the on-disk map library (default library-data/)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open a browser tab on start",
    )
    parser.add_argument("--debug", action="store_true", help="run Flask in debug mode")
    args = parser.parse_args()

    # A port is "explicit" (a hard requirement) when the user set --port or $PORT;
    # otherwise it's the default and we may auto-advance if it's busy.
    port_explicit = ("--port" in sys.argv) or (env_port is not None)
    port = _resolve_port(args.host, args.port, port_explicit)

    app = create_app(library_root=args.library_root)

    url = f"http://{args.host}:{port}/"
    if not args.no_browser and not args.debug:
        # Open the browser only after the server is confirmed up, from a daemon
        # thread, so a failed bind never leaves the browser on a dead URL.
        threading.Thread(
            target=_open_browser_when_up, args=(url,), daemon=True
        ).start()

    print(f"TTRPG Grid Map Generator running at {url}")
    print("Fully offline — no network calls, no API keys, no ongoing cost.")
    app.run(host=args.host, port=port, debug=args.debug)


if __name__ == "__main__":
    main()
