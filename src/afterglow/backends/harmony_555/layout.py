"""Layout allocator and offset tracker for Harmony 555 (Architecture 9)."""

from __future__ import annotations

import struct
from . import ahcm, scaffold, structures


def place(out: bytearray, body: bytes) -> int:
    """Place a body in layout memory while respecting 64 KiB window boundaries."""
    offset = structures.align_window(out, len(body))
    out += body
    return offset


class Layout:
    """Maintains binary memory allocation state for Harmony 555 configuration images."""

    def __init__(self):
        # 1. Initialize 0x5B bytes for header and section relocation table
        self.out = bytearray(scaffold.CMAH_OFFSET)
        struct.pack_into("<4s", self.out, 0, ahcm.MAGIC)
        struct.pack_into("<I", self.out, 4, 0)  # Sealed later by ahcm.seal()
        struct.pack_into("<I", self.out, 8, ahcm.HEADER_CONSTANT)

        # 2. Append CMAH marker (0x5B..0x5E)
        self.out += structures.CMAH_MARKER

        # 3. Canonical empty register at offset 0x5F (0x5F..0x60)
        self.empty_key_register = len(self.out)
        self.out += b"\x00\x00"

        # Allocation lists
        self.objects: list[bytes] = []
        self.trailers: list[int] = []
        self.pending_slots: list[tuple] = []
        self.placed_programs: dict[int, int] = {}

    def allocate_object(self, atoms: list[tuple[int, int]]) -> int:
        """Register an execution object body and return its object index."""
        body = structures.object_body(atoms)
        self.objects.append(body)
        return len(self.objects) - 1
