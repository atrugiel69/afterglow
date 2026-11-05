"""Binary re-assembly pipeline for Harmony 555 (Architecture 9).

Bodies are placed into the variable data heap (0x00061+) first. Then the master
section tables (00 through 17) are placed contiguously in ascending order,
the relocation table at 0x0C is patched, and the container is sealed with
the XOR checksum and MCHA trailer.
"""

from __future__ import annotations

import struct
from typing import Any
from . import ahcm, drawing, layout, scaffold, structures
from .layout import Layout, place


def place_key_register(lay: Layout, entries: list[dict | tuple]) -> int:
    """Assemble and place a hardware scan-code key register."""
    if not entries:
        return lay.empty_key_register
    body = structures.key_register(entries)
    return place(lay.out, body)


def place_objects(lay: Layout, object_atom_lists: list[list[tuple[int, int]]]) -> list[int]:
    """Place execution object bodies into layout memory and return their offsets."""
    offsets = []
    for atoms in object_atom_lists:
        body = structures.object_body(atoms)
        offsets.append(place(lay.out, body))
    return offsets


def write_tables(
    lay: Layout,
    *,
    sections: dict[int, bytes],
) -> dict[int, int]:
    """Place master section tables (00..17) contiguously in ascending order and update AHCM header."""
    out = lay.out
    placed: dict[int, int] = {}

    for index in range(ahcm.SECTION_COUNT):
        if index in sections:
            offset = len(out)
            out += sections[index]
            placed[index] = offset
        elif index in scaffold.CONSTANT_SECTIONS:
            offset = len(out)
            out += scaffold.CONSTANT_SECTIONS[index]
            placed[index] = offset

    # Patch 32-bit flash pointers into the AHCM header table at 0x0C + 4 * index
    for index, offset in placed.items():
        struct.pack_into("<I", out, 0x0C + 4 * index, ahcm.BASE + offset)

    return placed


def seal_image(lay: Layout) -> bytes:
    """Compute the dual-lane XOR checksum, patch end vector, and append MCHA trailer."""
    return ahcm.seal(lay.out)


def image(
    lay: Layout,
    *,
    screens: list[dict],
    objects: list[list[tuple[int, int]]],
    sections: dict[int, bytes] | None = None,
) -> bytes:
    """Assemble screens with pointer relocation, execution objects, and master section tables."""
    # -------------------------------------------------------------------------
    # PASS 1: Pre-calculate screen layout using a shadow bytearray to match
    # all 64 KiB window boundary alignments.
    # -------------------------------------------------------------------------
    addr_map: dict[int, int] = {}
    string_map: dict[str, int] = {}

    dummy = bytearray(len(lay.out))

    for screen in screens:
        # 1. Pre-simulate header key register
        header_keys = screen.get("header_keys", [])
        if header_keys:
            place(dummy, structures.key_register(header_keys))

        # 2. Pre-simulate slots and drawing programs
        for slot in screen.get("slots", []):
            keys = slot.get("keys", [])
            if keys:
                place(dummy, structures.key_register(keys))

            insts = slot.get("instructions", [])
            prog_bytes = drawing.assemble(insts)
            prog_pos = place(dummy, prog_bytes)

            # Register inline string offsets
            cursor = prog_pos
            for inst in insts:
                inst_sz = len(drawing.encode_instruction(inst))
                op = inst.get("op") or inst.get("opcode")
                if op == "inline_text":
                    str_flash = ahcm.BASE + cursor + 3
                    if "offset" in inst:
                        addr_map[inst["offset"] + 3] = str_flash
                    if inst.get("text"):
                        string_map[inst["text"]] = str_flash
                cursor += inst_sz

            # 6-byte slot record
            place(dummy, bytes(6))

        # 3. Pre-simulate screen trailer
        slot_cnt = len(screen.get("slots", []))
        place(dummy, bytes(1 + 3 + 2 + 3 * slot_cnt))

    # -------------------------------------------------------------------------
    # PASS 2: Place relocated screen programs, slots, and trailers into layout
    # -------------------------------------------------------------------------
    for screen in screens:
        header_offset = place_key_register(lay, screen.get("header_keys", []))
        slot_offsets = []

        for slot in screen.get("slots", []):
            kreg_off = place_key_register(lay, slot.get("keys", []))

            relocated_insts = drawing.relocated(
                slot.get("instructions", []),
                mapping=addr_map,
                strings=string_map,
            )
            bytecode = drawing.assemble(relocated_insts)
            prog_off = place(lay.out, bytecode)

            slot_bytes = structures.screen_slot(kreg_off, prog_off)
            slot_off = place(lay.out, slot_bytes)
            slot_offsets.append(slot_off)

        trailer_bytes = structures.screen_trailer(
            screen.get("flags", 0),
            header_offset,
            slot_offsets,
        )
        trailer_off = place(lay.out, trailer_bytes)
        lay.trailers.append(trailer_off)

    # 3. Place execution objects
    obj_offsets = place_objects(lay, objects)

    # 4. Build Section 06 (Screens) and Section 10 (Objects) indexes
    all_sections = dict(sections or {})
    all_sections[6] = structures.screen_index(lay.trailers)
    all_sections[10] = structures.object_index(obj_offsets)

    # 5. Write master section tables and seal container
    write_tables(lay, sections=all_sections)
    return seal_image(lay)
