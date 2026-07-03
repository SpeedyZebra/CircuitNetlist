from __future__ import annotations

import argparse
import threading
import webbrowser
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Circuit Netlist viewer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window automatically.")
    return parser.parse_args()


def open_browser(url: str) -> None:
    webbrowser.open(url)


if __name__ == "__main__":
    args = parse_args()
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, open_browser, args=(url,)).start()
    uvicorn.run("circuit_netlist.app:app", host=args.host, port=args.port, reload=False)
