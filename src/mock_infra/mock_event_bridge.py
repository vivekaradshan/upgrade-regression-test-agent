"""Simulates EventBridge: registered callbacks fire when a matching event is emitted."""

from __future__ import annotations

from typing import Callable


class MockEventBridge:
    def __init__(self):
        self._callbacks: list[tuple[str, Callable[[dict], None]]] = []

    def register_callback(self, event_pattern: str, callback: Callable[[dict], None]) -> None:
        self._callbacks.append((event_pattern, callback))

    def emit_event(self, source: str, detail_type: str, detail: dict) -> None:
        event = {"source": source, "detail-type": detail_type, "detail": detail}
        for pattern, callback in self._callbacks:
            if pattern in ("*", source, detail_type):
                callback(event)
