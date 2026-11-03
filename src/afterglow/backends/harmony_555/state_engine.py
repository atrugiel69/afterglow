"""Section 13 State Engine parser for Harmony 555 (Architecture 9)."""

from __future__ import annotations

import struct
from . import ahcm


def _u24(payload: bytes, offset: int) -> int:
    return int.from_bytes(payload[offset : offset + 3], "little")


def inspect(payload: bytes) -> dict:
    """Read Section 13 property records and their state transitions."""
    sec13_start = struct.unpack_from("<I", payload, 0x0C + 13 * 4)[0] - ahcm.BASE
    count = struct.unpack_from("<H", payload, sec13_start)[0]

    properties = []
    for prop_id in range(count):
        pos = sec13_start + 8 + 3 * prop_id
        ptr = _u24(payload, pos) - ahcm.BASE
        initial, limit, tcount = struct.unpack_from("<HHH", payload, ptr)

        transitions = []
        for t in range(tcount):
            at = ptr + 7 + 8 * t
            flags, src, dst, operand, category = struct.unpack_from(
                "<BHHHB", payload, at
            )
            transitions.append({
                "index": t,
                "flags": flags,
                "from_state": src,
                "to_state": dst,
                "operand": operand,
                "category": category,
            })

        properties.append({
            "property_id": prop_id,
            "offset": ptr,
            "initial": initial,
            "limit": limit,
            "transition_count": tcount,
            "transitions": transitions,
        })

    return {
        "property_count": count,
        "properties": properties,
    }
