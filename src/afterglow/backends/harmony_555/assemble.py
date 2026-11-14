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


def place_commands(lay: Layout, devices: list[dict]) -> list[list[int]]:
    """Place command records and tokenized waveforms into heap memory."""
    out = lay.out
    device_records = []
    for dev in devices:
        placed = []
        for cmd in dev.get("commands", []):
            # If pre-compiled raw record and waveforms are provided
            if "raw_record" in cmd:
                rec_off = place(out, cmd["raw_record"])
                placed.append(rec_off)
            elif "record_offset" in cmd:
                placed.append(cmd["record_offset"])
        device_records.append(placed)
    return device_records


def write_tables(
    lay: Layout,
    *,
    sections: dict[int, bytes],
    device_records: list[list[int]] | None = None,
) -> dict[int, int]:
    """Place master section tables (00..17) contiguously in ascending order and update AHCM header."""
    out = lay.out
    placed: dict[int, int] = {}

    for index in range(ahcm.SECTION_COUNT):
        if index == 4 and 4 not in sections and device_records is not None:
            offset = len(out)
            static_head = scaffold.section_04_static()
            sec4_bytes = structures.command_flat_index(device_records, static_head=static_head)
            out += sec4_bytes
            placed[4] = offset
        elif index == 5 and 5 not in sections and device_records is not None:
            offset = len(out)
            run = placed[4] + scaffold.SECTION_04_STATIC_SIZE
            subtable_offsets = []
            for cmds in device_records:
                subtable_offsets.append(run)
                run += len(structures.command_subtable(cmds))
            sec5_bytes = structures.device_master_table(subtable_offsets)
            out += sec5_bytes
            placed[5] = offset
        elif index in sections:
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

    # Relocate 24-bit flash pointers inside Sections 00..17 when section offsets shift
    donor_bytes = scaffold.scaffold_payload_path().read_bytes()
    orig_offs = [
        struct.unpack_from("<I", donor_bytes, 0x0C + i * 4)[0] - ahcm.BASE
        for i in range(ahcm.SECTION_COUNT)
    ]
    orig_ends = [
        orig_offs[i + 1] if i + 1 < ahcm.SECTION_COUNT else len(donor_bytes) - 6
        for i in range(ahcm.SECTION_COUNT)
    ]
    deltas = {
        i: placed[i] - orig_offs[i]
        for i in range(ahcm.SECTION_COUNT)
        if i in placed
    }

    def _reloc_u24_at(pos: int, allowed_sections: tuple[int, ...]) -> int:
        val = out[pos] | (out[pos + 1] << 8) | (out[pos + 2] << 16)
        rel = val - ahcm.BASE
        for s_idx in allowed_sections:
            if orig_offs[s_idx] <= rel < orig_ends[s_idx]:
                d = deltas.get(s_idx, 0)
                if d:
                    val += d
                    out[pos] = val & 0xFF
                    out[pos + 1] = (val >> 8) & 0xFF
                    out[pos + 2] = (val >> 16) & 0xFF
                return val
        return val

    # Section 04 static header pointers (and Section 05 if copied verbatim)
    if 4 in placed and deltas.get(4, 0) != 0:
        for rel in range(178, min(250, scaffold.SECTION_04_STATIC_SIZE - 2), 3):
            _reloc_u24_at(placed[4] + rel, (4,))
        if 5 in placed and 5 not in sections and device_records is None:
            dev_cnt = out[placed[5]]
            for d in range(dev_cnt):
                _reloc_u24_at(placed[5] + 1 + 3 * d, (4,))

    # Section 06 screen index, un-recompiled trailers, slot records, and key-register pointers
    if 6 in placed:
        sec6_start = placed[6]
        orig_sec6_start = orig_offs[6]
        scr_cnt = struct.unpack_from("<H", out, sec6_start)[0]
        orig_scr_cnt = struct.unpack_from("<H", donor_bytes, orig_sec6_start)[0]
        for s in range(min(scr_cnt, orig_scr_cnt)):
            tr_ptr_pos = sec6_start + 3 + 3 * s
            orig_tr_ptr_pos = orig_sec6_start + 3 + 3 * s
            # Skip screens whose trailer pointer was replaced by screen_compiler
            if bytes(out[tr_ptr_pos : tr_ptr_pos + 3]) != donor_bytes[orig_tr_ptr_pos : orig_tr_ptr_pos + 3]:
                continue
            new_tr = _reloc_u24_at(tr_ptr_pos, (6,))
            tr_off = new_tr - ahcm.BASE
            if not (0 <= tr_off + 6 <= len(out)):
                continue
            _reloc_u24_at(tr_off + 1, (6, 8))
            slot_cnt = struct.unpack_from("<H", out, tr_off + 4)[0]
            for sl in range(slot_cnt):
                sl_ptr_pos = tr_off + 6 + 3 * sl
                if sl_ptr_pos + 3 > len(out):
                    break
                new_sl = _reloc_u24_at(sl_ptr_pos, (6,))
                s_off = new_sl - ahcm.BASE
                if 0 <= s_off + 6 <= len(out):
                    _reloc_u24_at(s_off, (6, 8))
                    _reloc_u24_at(s_off + 3, (6, 8))

    # Section 07 screen pointers into Section 06
    if 7 in placed and 7 not in sections and deltas.get(6, 0) != 0:
        cnt7 = struct.unpack_from("<H", out, placed[7])[0]
        for i in range(cnt7):
            _reloc_u24_at(placed[7] + 2 + 3 * i, (6,))

    # Section 09 mode pointers into Section 09 (skip modes freshly allocated in lay.out)
    if 9 in placed and deltas.get(9, 0) != 0:
        sec9_start = placed[9]
        orig_sec9_start = orig_offs[9]
        mode_cnt = out[sec9_start]
        orig_mode_cnt = donor_bytes[orig_sec9_start]
        for m in range(min(mode_cnt, orig_mode_cnt)):
            m_ptr_pos = sec9_start + 1 + 3 * m
            orig_m_ptr_pos = orig_sec9_start + 1 + 3 * m
            if bytes(out[m_ptr_pos : m_ptr_pos + 3]) != donor_bytes[orig_m_ptr_pos : orig_m_ptr_pos + 3]:
                continue
            _reloc_u24_at(m_ptr_pos, (9,))

    # Section 15 pointers into Section 14
    if 15 in placed and 15 not in sections and deltas.get(14, 0) != 0:
        cnt15 = out[placed[15]]
        for i in range(cnt15):
            _reloc_u24_at(placed[15] + 1 + 3 * i, (14,))

    return placed


def seal_image(lay: Layout) -> bytes:
    """Compute the dual-lane XOR checksum, patch end vector, and append MCHA trailer."""
    return ahcm.seal(lay.out)


def image(
    lay: Layout,
    *,
    screens: list[dict],
    objects: list[list[tuple[int, int]]],
    device_records: list[list[int]] | None = None,
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
    write_tables(lay, sections=all_sections, device_records=device_records)
    return seal_image(lay)
