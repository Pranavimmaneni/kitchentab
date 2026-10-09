"""UUIDv7 primary keys (RFC 9562), generated in the app: Postgres 16 has no native uuidv7().

Within one millisecond the 12-bit `rand_a` field is a counter, so ids from one process sort in
creation order.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = -1
_counter = 0


def uuid7() -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            _counter += 1
            if _counter > 0xFFF:  # counter exhausted: borrow the next millisecond
                _last_ms += 1
                _counter = 0
            ms = _last_ms
        else:
            _last_ms = ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x3FF  # leave headroom for increments
        rand_a = _counter
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)
