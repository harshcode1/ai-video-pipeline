"""Watch input/ and fire render.py --all automatically once BOTH
Script.txt and Visuals.txt are present. Debounced so writing the two
files a moment apart doesn't trigger two overlapping renders."""
import subprocess
import sys
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import config

DEBOUNCE_SECONDS = 3.0


class InputHandler(FileSystemEventHandler):
    def __init__(self):
        self._timer = None
        self._lock = threading.Lock()
        self._render_running = False

    def _both_present(self) -> bool:
        return config.SCRIPT_FILE.exists() and config.VISUALS_FILE.exists()

    def _on_change(self):
        with self._lock:
            if not self._both_present():
                return
            if self._render_running:
                print("[watch] render already in progress, ignoring change")
                return
            self._render_running = True

        print("[watch] both input files present -> starting render.py --all")
        try:
            result = subprocess.run(
                [sys.executable, str(config.ROOT / "src" / "render.py"), "--all"],
                cwd=str(config.ROOT),
            )
            if result.returncode == 0:
                print("[watch] render complete")
            else:
                print(f"[watch] render.py exited with code {result.returncode}")
        finally:
            with self._lock:
                self._render_running = False

    def _debounced_trigger(self):
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(DEBOUNCE_SECONDS, self._on_change)
        self._timer.daemon = True
        self._timer.start()

    def on_created(self, event):
        if not event.is_directory:
            self._debounced_trigger()

    def on_modified(self, event):
        if not event.is_directory:
            self._debounced_trigger()


def main():
    config.ensure_dirs()
    config.INPUT_DIR.mkdir(parents=True, exist_ok=True)
    handler = InputHandler()
    observer = Observer()
    observer.schedule(handler, str(config.INPUT_DIR), recursive=False)
    observer.start()
    print(f"[watch] watching {config.INPUT_DIR} for Script.txt + Visuals.txt ...")

    if handler._both_present():
        print("[watch] both files already present at startup -> triggering render")
        handler._debounced_trigger()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
