from __future__ import annotations

import secrets
import time
import uuid


def uuid7(timestamp_ms: int | None = None) -> str:
    """Return a canonical time-ordered UUIDv7 string.

    Python 3.13 does not expose uuid.uuid7, so Forge X creates the RFC 9562
    bit layout directly and can replace this helper when the standard-library
    implementation becomes available.
    """

    milliseconds = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= milliseconds < 1 << 48:
        raise ValueError("UUIDv7 timestamp must fit in 48 bits")
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = milliseconds << 80
    value |= 0x7 << 76
    value |= random_a << 64
    value |= 0b10 << 62
    value |= random_b
    return str(uuid.UUID(int=value))
