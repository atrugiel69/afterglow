"""AHCM container validation and sealing for Logitech Harmony 555 (Architecture 9)."""

from __future__ import annotations

import struct

BASE = 0x020000
MAGIC = b"AHCM"
TRAILER_MAGIC = b"MCHA"
HEADER_CONSTANT = 0x1400
SECTION_COUNT = 20
CHECKSUM_SEED = (0x21, 0x43)


def checksum(data: bytes, end: int) -> tuple[int, int]:
    """Compute the two-lane XOR checksum over complete pairs before end."""
    end -= end % 2
    low_seed, high_seed = CHECKSUM_SEED

    lane_low = 0
    for b in data[0:end:2]:
        lane_low ^= b

    lane_high = 0
    for b in data[1:end:2]:
        lane_high ^= b

    return (low_seed ^ lane_low, high_seed ^ lane_high)


def seal(body: bytes) -> bytes:
    """Close an AHCM image with the checksum and MCHA trailer marker."""
    out = bytearray(body)
    marker_pos = len(out) + 2

    # Append padding/checksum slot and MCHA trailer
    out += b"\x00\x00" + TRAILER_MAGIC

    # Update header end vector at offset 0x04
    struct.pack_into("<I", out, 4, BASE + marker_pos)

    # Compute and insert checksum
    ck_low, ck_high = checksum(out, marker_pos - 2)
    out[marker_pos - 2] = ck_low
    out[marker_pos - 1] = ck_high

    return bytes(out)


def validate(payload: bytes) -> dict:
    """Validate header, section table, end vector, and checksum of an AHCM image."""
    if len(payload) < 0x5C:
        raise ValueError("Payload shorter than AHCM header and section index")
    if payload[:4] != MAGIC:
        raise ValueError(f"Invalid AHCM magic: {payload[:4]!r}")

    end_vector, constant = struct.unpack_from("<II", payload, 4)
    if constant != HEADER_CONSTANT:
        raise ValueError(
            f"Invalid header constant {constant:#x}, expected {HEADER_CONSTANT:#x}"
        )

    marker_offset = end_vector - BASE
    if marker_offset < 2 or marker_offset + 4 > len(payload):
        raise ValueError(
            f"AHCM end vector {end_vector:#08x} falls outside binary"
        )
    if payload[marker_offset : marker_offset + 4] != TRAILER_MAGIC:
        raise ValueError(
            f"AHCM trailer marker at {marker_offset:#x} is not {TRAILER_MAGIC!r}"
        )

    ck_offset = marker_offset - 2
    computed = checksum(payload, ck_offset)
    stored = (payload[ck_offset], payload[ck_offset + 1])
    if computed != stored:
        raise ValueError(
            f"Checksum mismatch: computed {computed[0]:02x}{computed[1]:02x}, "
            f"stored {stored[0]:02x}{stored[1]:02x}"
        )

    return {"ok": True, "size": len(payload), "end_vector": end_vector}