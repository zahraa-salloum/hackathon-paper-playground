"""Run `python -m meeting_assistant --demo` from the project directory."""

import argparse
import os
from pathlib import Path
import sys
import threading
import webbrowser

from . import __version__
from .demo import seed_demo
from .server import AppServer


def main():
    parser = argparse.ArgumentParser(description="Minutes — a local context-aware meeting assistant")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--port", type=int, default=8765, help="Local port (default: 8765; 0 selects a free port)")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("MINUTES_DATA_DIR", str(Path.home() / ".meeting-assistant"))))
    parser.add_argument("--demo", action="store_true", help="Load three original synthetic demo meetings")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening a browser")
    parser.add_argument("--desktop", action="store_true", help="Open an optional pywebview native desktop window")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        server = AppServer(("127.0.0.1", args.port), args.data_dir)
    except OSError as exc:
        print(f"Could not start Minutes: {exc}. Try --port 8766.", file=sys.stderr)
        return 1
    if args.demo:
        seed_demo(server.store)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Minutes {__version__} is running at {url}", flush=True)
    print(f"Archive: {args.data_dir.resolve()}\nPress Ctrl+C to stop.", flush=True)
    if args.desktop:
        try:
            import webview
        except ImportError:
            print("Desktop window needs: python -m pip install '.[desktop]'", file=sys.stderr)
            server.server_close()
            return 1
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            webview.create_window("Minutes · Meeting assistant", url, width=1360, height=900, min_size=(800, 600))
            webview.start()
        finally:
            server.shutdown()
            server.server_close()
    else:
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping Minutes...", flush=True)
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
