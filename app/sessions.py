"""Tiny in-memory session store keyed by `session_id`.

Holds the last itinerary and the parsed `IntentPlan` for every active tour
so that `/refine` can build on prior context without forcing the iOS
Shortcut to ship the full state on each call.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from .models import IntentPlan, Itinerary


@dataclass
class SessionRecord:
    session_id: str
    query: str
    intent: IntentPlan
    itinerary: Itinerary
    history: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class SessionStore:
    """Thread-safe in-memory store. Sessions auto-expire after `ttl_seconds`."""

    def __init__(self, ttl_seconds: int = 60 * 60 * 24) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def create(self, query: str, intent: IntentPlan, itinerary: Itinerary) -> str:
        session_id = uuid.uuid4().hex[:12]
        record = SessionRecord(
            session_id=session_id,
            query=query,
            intent=intent,
            itinerary=itinerary,
            history=[query],
        )
        with self._lock:
            self._gc_locked()
            self._sessions[session_id] = record
        return session_id

    def get(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            self._gc_locked()
            return self._sessions.get(session_id)

    def update(
        self,
        session_id: str,
        *,
        instruction: str,
        intent: IntentPlan,
        itinerary: Itinerary,
    ) -> SessionRecord | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            record.intent = intent
            record.itinerary = itinerary
            record.history.append(instruction)
            record.updated_at = time.time()
            return record

    def _gc_locked(self) -> None:
        cutoff = time.time() - self._ttl
        stale = [k for k, v in self._sessions.items() if v.updated_at < cutoff]
        for k in stale:
            self._sessions.pop(k, None)
