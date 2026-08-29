from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Iterator


@dataclass(frozen=True)
class ComponentTrace:
    session_id: str
    turn: int
    component: str
    elapsed_ms: float
    success: bool
    fallback_used: bool
    error_type: str | None = None


class RuntimeTraceRecorder:
    """Bounded, opt-in internal tracing that never changes the response schema."""

    def __init__(self, enabled: bool = False, limit: int = 5000) -> None:
        self.enabled = bool(enabled)
        self._records: deque[ComponentTrace] = deque(maxlen=max(1, int(limit)))

    @contextmanager
    def measure(self, session_id: str, turn: int, component: str) -> Iterator[dict]:
        if not self.enabled:
            yield {}
            return
        outcome: dict = {"success": True, "fallback_used": False, "error_type": None}
        started = time.perf_counter()
        try:
            yield outcome
        except Exception as exc:
            outcome["success"] = False
            outcome["error_type"] = type(exc).__name__
            raise
        finally:
            self.record(
                session_id,
                turn,
                component,
                (time.perf_counter() - started) * 1000.0,
                success=bool(outcome.get("success", True)),
                fallback_used=bool(outcome.get("fallback_used", False)),
                error_type=outcome.get("error_type"),
            )

    def record(
        self,
        session_id: str,
        turn: int,
        component: str,
        elapsed_ms: float,
        *,
        success: bool = True,
        fallback_used: bool = False,
        error_type: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        self._records.append(ComponentTrace(
            session_id=str(session_id),
            turn=int(turn),
            component=str(component),
            elapsed_ms=round(float(elapsed_ms), 6),
            success=bool(success),
            fallback_used=bool(fallback_used),
            error_type=error_type,
        ))

    def records(self) -> list[dict]:
        return [asdict(record) for record in self._records]

    def clear(self) -> None:
        self._records.clear()
