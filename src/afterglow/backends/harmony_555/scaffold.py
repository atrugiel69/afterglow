"""Scaffold and platform constants for Logitech Harmony 555 (Architecture 9)."""

from __future__ import annotations

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
