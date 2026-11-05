"""Scaffold and platform constants for Logitech Harmony 555 (Architecture 9)."""

from __future__ import annotations

from pathlib import Path

# Architecture 9 constant sections measured from donor images
CONSTANT_SECTIONS: dict[int, bytes] = {
    # Sec 01: Hardware protocol and firmware revision vector (Architecture 9)
    1: bytes.fromhex("0909300d000000"),
    # Sec 02: Hardware profile and memory alignment window
    2: bytes.fromhex("0020000007000008"),
    # Sec 16: Platform terminator vector
    16: bytes.fromhex("00"),
}

# Container structural geometry
HEADER_SIZE = 0x5C
CMAH_OFFSET = 0x5B
CANONICAL_EMPTY_REGISTER = 0x5F
HEAP_START_OFFSET = 0x61

# Section 04 static timing dictionary block length
SECTION_04_STATIC_SIZE = 7012


def section_04_static(payload: bytes | None = None) -> bytes:
    """Extract or return the 7,012-byte Architecture 9 Section 04 static dictionary block."""
    if payload is not None:
        sec4_flash = int.from_bytes(payload[0x0C + 4 * 4 : 0x10 + 4 * 4], "little")
        sec4_rel = sec4_flash - 0x20000
        return payload[sec4_rel : sec4_rel + SECTION_04_STATIC_SIZE]
    scaffold_payload = Path(__file__).resolve().parent.parent.parent / "scaffolds" / "harmony-555" / "payload.bin"
    raw = scaffold_payload.read_bytes()
    sec4_flash = int.from_bytes(raw[0x0C + 4 * 4 : 0x10 + 4 * 4], "little")
    sec4_rel = sec4_flash - 0x20000
    return raw[sec4_rel : sec4_rel + SECTION_04_STATIC_SIZE]
