from __future__ import annotations

import threading
import webbrowser
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def open_browser() -> None:
    webbrowser.open("http://127.0.0.1:8000")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    uvicorn.run("circuit_netlist.app:app", host="127.0.0.1", port=8000, reload=False)
