"""In-process event bus + live event stream.

Every agent transition and inter-agent message is published here. The HTTP layer
subscribes to stream Server-Sent Events to the live map. On server, swap the transport
for Redis pub/sub or Kafka by implementing the same publish()/subscribe() surface.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Iterator


@dataclass
class Event:
    type: str                    # node_update | message | workflow | ingest | log
    request_id: str = ""
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def sse(self) -> str:
        return f"event: {self.type}\ndata: {json.dumps(asdict(self))}\n\n"


class EventBus:
    def __init__(self, history: int = 500) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._history: deque[Event] = deque(maxlen=history)

    def publish(self, ev: Event) -> None:
        with self._lock:
            self._history.append(ev)
            dead = []
            for q in self._subs:
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subs.remove(q)

    def subscribe(self) -> "Subscription":
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            # replay recent history so a late-joining UI sees current state
            for ev in list(self._history):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    break
            self._subs.append(q)
        return Subscription(self, q)

    def _unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def recent(self, request_id: str | None = None) -> list[Event]:
        with self._lock:
            evs = list(self._history)
        return [e for e in evs if request_id is None or e.request_id == request_id]


class Subscription:
    def __init__(self, bus: EventBus, q: queue.Queue) -> None:
        self._bus, self._q = bus, q

    def stream(self, keepalive_s: float = 15.0) -> Iterator[Event]:
        while True:
            try:
                yield self._q.get(timeout=keepalive_s)
            except queue.Empty:
                yield Event(type="log", data={"keepalive": True})

    def close(self) -> None:
        self._bus._unsubscribe(self._q)


# Global bus (single-process local runtime).
bus = EventBus()
