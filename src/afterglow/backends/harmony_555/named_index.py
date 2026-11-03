"""Parse the Section 00 named-record table for Harmony 555 (Architecture 9)."""

from __future__ import annotations

import struct
from . import ahcm


def section_0_range(payload: bytes) -> tuple[int, int]:
    """Return (start, end) byte offsets for Section 00 from the master table."""
    start = struct.unpack_from("<I", payload, 0x0C)[0] - ahcm.BASE
    end = struct.unpack_from("<I", payload, 0x10)[0] - ahcm.BASE
    return start, end


def inspect(payload: bytes) -> dict:
    """Parse every record in Section 00 enclosed between FEED and BEEF markers."""
    start, end = section_0_range(payload)
    if end - start < 7 or payload[start : start + 2] != b"\xed\xfe":
        raise ValueError("Section 00 missing FEED (0xEDFE) header marker")

    declared = int.from_bytes(payload[start + 2 : start + 5], "little")
    if declared != end - start - 2 and not (end - start == 7 and declared == 0):
        raise ValueError(
            f"Section 00 declared {declared} bytes, expected {end - start - 2}"
        )

    cursor = start + 5
    records = []

    while cursor < end:
        if payload[cursor : cursor + 2] == b"\xef\xbe":
            cursor += 2
            break

        if payload[cursor] != 0xA7:
            raise ValueError(f"Corrupt record tag {payload[cursor]:#x} at {cursor:#x}")

        length, record_type, identifier = struct.unpack_from("<HHH", payload, cursor + 1)
        record_end = cursor + 3 + length
        if record_end > end:
            raise ValueError(f"Record at {cursor:#x} exceeds section boundary")

        raw_name = payload[cursor + 7 : record_end]
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            name = raw_name.hex()

        records.append({
            "offset": cursor,
            "type": record_type,
            "id": identifier,
            "name": name,
        })
        cursor = record_end

    return {"records": records, "count": len(records)}
