"""Section 06 Screen & Soft-Button Bytecode Compiler for Architecture 9."""

from __future__ import annotations

import struct
from . import ahcm, drawing

BASE = ahcm.BASE
QUAD_KEYS = [0xA7, 0xA6, 0x9F, 0x9E]
QUAD_NAMES = ["top_left", "top_right", "bottom_left", "bottom_right"]


def generate_slot_drawing_program(
    title: str,
    page_idx: int,
    page_count: int,
    quad_labels: dict[str, str],
) -> bytes:
    """Assemble an 8-band drawing program for one 4-quadrant soft-button page."""
    insts = []
    x_title = max(0, (96 - len(title) * 6) // 2)

    # Band 0: Title bar top
    insts.append({"op": "box", "line": 0, "type": 3, "x": 0, "y": 0, "w": 96, "h": 8, "style": 0})
    insts.append({"op": "font", "opcode": 0x10, "font_id": 1})
    if title:
        insts.append({"op": "inline_text", "x": x_title, "y": 0, "text": title, "glyphs": drawing.encode_glyphs(title)})
    insts.append({"op": "return"})

    # Band 1: Title bar bottom + divider + Top buttons
    insts.append({"op": "box", "line": 1, "type": 3, "x": 2048, "y": 2048, "w": 96, "h": 8, "style": 0})
    if title:
        insts.append({"op": "inline_text", "x": x_title, "y": 0, "text": title, "glyphs": drawing.encode_glyphs(title)})
    insts.append({"op": "line", "line_type": 0, "geom": bytes.fromhex("0c00006001"), "style": 0})
    insts.append({"op": "font", "opcode": 0x10, "font_id": 0})

    tl = quad_labels.get("top_left", "")
    tr = quad_labels.get("top_right", "")
    tr_x = max(40, 95 - len(tr) * 6) if tr else 67

    if tl:
        insts.append({"op": "inline_text", "x": 0, "y": 13, "text": tl, "glyphs": drawing.encode_glyphs(tl)})
    if tr:
        insts.append({"op": "inline_text", "x": tr_x, "y": 13, "text": tr, "glyphs": drawing.encode_glyphs(tr)})
    insts.append({"op": "return"})

    # Bands 2 & 3: Top buttons (vertical raster persistence)
    for band_idx, coord in [(2, 4096), (3, 6144)]:
        insts.append({"op": "box", "line": band_idx, "type": 3, "x": coord, "y": coord, "w": 96, "h": 8, "style": 0})
        if tl:
            insts.append({"op": "inline_text", "x": 0, "y": 13, "text": tl, "glyphs": drawing.encode_glyphs(tl)})
        if tr:
            insts.append({"op": "inline_text", "x": tr_x, "y": 13, "text": tr, "glyphs": drawing.encode_glyphs(tr)})
        insts.append({"op": "return"})

    # Bands 4 & 5: Bottom buttons
    bl = quad_labels.get("bottom_left", "")
    br = quad_labels.get("bottom_right", "")
    br_x = max(40, 95 - len(br) * 6) if br else 67

    for band_idx, coord in [(4, 8192), (5, 10240)]:
        insts.append({"op": "box", "line": band_idx, "type": 3, "x": coord, "y": coord, "w": 96, "h": 8, "style": 0})
        if bl:
            insts.append({"op": "inline_text", "x": 0, "y": 35, "text": bl, "glyphs": drawing.encode_glyphs(bl)})
        if br:
            insts.append({"op": "inline_text", "x": br_x, "y": 35, "text": br, "glyphs": drawing.encode_glyphs(br)})
        insts.append({"op": "return"})

    # Band 6: Spacer band
    insts.append({"op": "box", "line": 6, "type": 3, "x": 12288, "y": 12288, "w": 96, "h": 8, "style": 0})
    insts.append({"op": "return"})

    # Band 7: Pagination footer (X OF Y)
    insts.append({"op": "box", "line": 7, "type": 3, "x": 14336, "y": 14336, "w": 96, "h": 8, "style": 0})
    if page_count > 1:
        insts.append({"op": "font", "opcode": 0x10, "font_id": 2})
        p_cur = str(page_idx + 1)
        p_tot = str(page_count)
        insts.append({"op": "inline_text", "x": 36, "y": 56, "text": p_cur, "glyphs": drawing.encode_glyphs(p_cur)})
        insts.append({"op": "inline_text", "x": 47, "y": 56, "text": "OF", "glyphs": drawing.encode_glyphs("OF")})
        insts.append({"op": "inline_text", "x": 65, "y": 56, "text": p_tot, "glyphs": drawing.encode_glyphs(p_tot)})
    insts.append({"op": "return"})

    # Band 8: End
    insts.append({"op": "end"})
    return drawing.assemble(insts)


def compile_activity_screen(
    lay,
    donor_payload: bytes,
    screen_idx: int,
    title: str,
    soft_buttons: list,
    device_name_to_idx: dict[str, int] | None = None,
    command_lookup: dict[tuple[int, int], int] | None = None,
) -> int:
    """Compile complete Section 06 screen records onto lay.out and return the new trailer flash pointer."""
    sec6_orig = struct.unpack_from("<I", donor_payload, 0x0C + 6 * 4)[0] - BASE
    tr_orig = int.from_bytes(donor_payload[sec6_orig + 3 + 3 * screen_idx : sec6_orig + 6 + 3 * screen_idx], "little") - BASE
    flags = donor_payload[tr_orig]
    hdr_kreg_flash = int.from_bytes(donor_payload[tr_orig + 1 : tr_orig + 4], "little")

    num_pages = max(1, (len(soft_buttons) + 3) // 4)
    slot_record_ptrs = []

    for page_idx in range(num_pages):
        page_slice = list(soft_buttons[page_idx * 4 : (page_idx + 1) * 4])
        while len(page_slice) < 4:
            page_slice.append(None)

        quad_labels = {}
        kreg_entries = []

        for quad_idx, entry in enumerate(page_slice):
            quad_name = QUAD_NAMES[quad_idx]
            kcode = QUAD_KEYS[quad_idx]

            if entry is None:
                continue

            lbl = None
            action_obj = None
            action_cat = 0x7F

            if isinstance(entry, str):
                lbl = entry
            elif isinstance(entry, dict):
                lbl = entry.get("label")
                action_obj = entry.get("operand", entry.get("object"))
                action_cat = entry.get("category", 0x7F)
                if action_obj is None and ("device" in entry or "command" in entry):
                    dev = entry.get("device", 0)
                    dev_idx = device_name_to_idx.get(dev, 0) if (device_name_to_idx and isinstance(dev, str)) else int(dev or 0)
                    cmd = entry.get("command", "")
                    cmd_idx = int(cmd.split("_")[-1]) if (isinstance(cmd, str) and "Command_" in cmd) else int(cmd or 0)
                    action_obj = command_lookup.get((dev_idx, cmd_idx), (dev_idx << 8) | cmd_idx) if command_lookup else ((dev_idx << 8) | cmd_idx)
            elif isinstance(entry, (list, tuple)):
                lbl = str(entry[0])

            if lbl:
                quad_labels[quad_name] = lbl

            if action_obj is not None:
                kreg_entries.append((kcode, action_obj, action_cat))

        prog_bytes = generate_slot_drawing_program(title, page_idx, num_pages, quad_labels)
        prog_off = len(lay.out)
        lay.out.extend(prog_bytes)
        prog_flash = BASE + prog_off

        kreg_bytes = bytearray([len(kreg_entries)])
        for kc, op, cat in kreg_entries:
            kreg_bytes.extend(struct.pack("<BHB", kc, op, cat))
        kreg_off = len(lay.out)
        lay.out.extend(kreg_bytes)
        kreg_flash = BASE + kreg_off

        slot_rec = kreg_flash.to_bytes(3, "little") + prog_flash.to_bytes(3, "little")
        slot_rec_off = len(lay.out)
        lay.out.extend(slot_rec)
        slot_record_ptrs.append(BASE + slot_rec_off)

    trailer = bytearray([flags]) + hdr_kreg_flash.to_bytes(3, "little") + struct.pack("<H", num_pages)
    for s_ptr in slot_record_ptrs:
        trailer.extend(s_ptr.to_bytes(3, "little"))

    trailer_off = len(lay.out)
    lay.out.extend(trailer)
    return BASE + trailer_off
