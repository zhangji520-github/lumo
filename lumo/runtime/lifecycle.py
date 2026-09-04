from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Awaitable, Callable

Cleanup = Callable[[], Awaitable[None] | None]


@dataclass
class RuntimeLifecycle:
    _cleanups: list[Cleanup] = field(default_factory=list)
    _closed: bool = False

    def add(self, cleanup: Cleanup) -> None:
        if self._closed:
            raise RuntimeError("runtime lifecycle is already closed")
        self._cleanups.append(cleanup)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        for cleanup in reversed(self._cleanups):
            try:
                result = cleanup()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                errors.append(exc)
        self._cleanups.clear()
        if errors:
            raise ExceptionGroup("runtime cleanup failed", errors)
