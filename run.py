#!/usr/bin/env python3
"""Entry point for the TTRPG Grid Map Generator.

Starts the Flask app bound to 127.0.0.1 (loopback only — the app makes zero
network calls at runtime and is not intended to be exposed to a network).

Usage::

    python run.py                 # serve on http://127.0.0.1:5000
    PORT=8080 python run.py       # custom port
    python run.py --port 8080     # custom port (flag)
"""

from __future__ import annotations

import argparse
import os
import webbrowser

from server.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="TTRPG Grid Map Generator server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host to bind (default 127.0.0.1 — loopback only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "5000")),
        help="port to bind (default 5000, or $PORT)",
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

    app = create_app(library_root=args.library_root)

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser and not args.debug:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    print(f"TTRPG Grid Map Generator running at {url}")
    print("Fully offline — no network calls, no API keys, no ongoing cost.")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
