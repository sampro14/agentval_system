"""OpenTelemetry hook point (design doc §18).

Phase 1 keeps this a no-op context manager so agents are already instrumented at the right
boundaries; wiring a real OTel tracer/exporter replaces only this module.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    yield
