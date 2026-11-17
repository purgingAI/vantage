"""Scheduler / ingestion agent — rediscovers repository changes on a fixed cadence
(default 10 minutes) and refreshes the knowledge layers within the target window."""
from __future__ import annotations

import threading
import time

from ..events import Event
from .git_ingest import ingest_repo


class Scheduler:
    def __init__(self, platform, interval_s: int | None = None) -> None:
        self.platform = platform
        self.interval_s = interval_s or platform.settings.schedule_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_run: float | None = None

    def run_once(self) -> list[dict]:
        results = []
        repos = self.platform.settings.repos or [r["path"] for r in self.platform.meta.repos()]
        for path in repos:
            try:
                results.append(ingest_repo(self.platform, path,
                                           max_files=self.platform.settings.max_files))
            except Exception as e:  # never let one repo kill the loop
                self.platform.bus.publish(Event(type="log", data={"ingest_error": str(e), "repo": path}))
        self.last_run = time.time()
        return results

    def _loop(self) -> None:
        # If nothing is ingested yet, do an immediate first pass; otherwise wait one interval
        # so a warm start (data already mapped) doesn't redo heavy work on boot.
        if not self.platform.meta.repos():
            self.run_once()
        while not self._stop.is_set():
            if self._stop.wait(self.interval_s):
                break
            self.run_once()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="vantage-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
