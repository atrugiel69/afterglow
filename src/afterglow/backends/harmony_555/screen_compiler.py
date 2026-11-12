"""Section 06 Screen & Soft-Button Bytecode Compiler for Architecture 9."""

from __future__ import annotations

import struct
from . import ahcm, drawing

BASE = ahcm.BASE
QUAD_KEYS = [0x9E, 0x9F, 0xA7, 0xA6]
QUAD_NAMES = ["top_left", "top_right", "bottom_left", "bottom_right"]


def _split_button_lines(label: str, max_chars: int = 8) -> list[str]:
    """Split a soft-button label into up to 2 display lines for the 96x64 LCD."""
    if not label:
        return []
    if "\n" in label:
        parts = [p.strip() for p in label.split("\n") if p.strip()]
        return parts[:2]
    if len(label) <= max_chars:
        return [label]
    words = label.split()
    if len(words) >= 2:
        line1 = words[0]
        line2 = " ".join(words[1:])
        return [line1[:max_chars], line2[:max_chars]]
    return [label[:max_chars], label[max_chars : max_chars * 2]]


def generate_slot_drawing_program(
    title: str,
    page_idx: int,
    page_count: int,
    quad_labels: dict[str, str],
) -> bytes:
    """Assemble an 8-band drawing program for one 4-quadrant soft-button page.

    Font 0 glyphs are 11px tall and span across multiple 8-pixel raster bands:
      - Top row Line 1 (y=13..23)  -> Bands 1, 2
      - Top row Line 2 (y=24..34)  -> Bands 3, 4
      - Bot row Line 1 (y=35..45)  -> Bands 4, 5
      - Bot row Line 2 (y=46..56)  -> Bands 5, 6, 7
    """
    insts = []
    x_title = max(0, (96 - len(title) * 6) // 2)

    tl_lines = _split_button_lines(quad_labels.get("top_left", ""))
    tr_lines = _split_button_lines(quad_labels.get("top_right", ""))
    bl_lines = _split_button_lines(quad_labels.get("bottom_left", ""))
    br_lines = _split_button_lines(quad_labels.get("bottom_right", ""))

    def _emit_quad_line(lines: list[str], line_idx: int, y: int, right_align: bool):
        if line_idx >= len(lines):
            return
        txt = lines[line_idx]
        x = max(40, 95 - len(txt) * 6) if right_align else 0
        insts.append({"op": "inline_text", "x": x, "y": y, "text": txt, "glyphs": drawing.encode_glyphs(txt)})

    # Band 0 (y=0..7): Title bar top
    insts.append({"op": "box", "line": 0, "type": 3, "x": 0, "y": 0, "w": 96, "h": 8, "style": 0})
    insts.append({"op": "font", "opcode": 0x10, "font_id": 1})
    if title:
        insts.append({"op": "inline_text", "x": x_title, "y": 0, "text": title, "glyphs": drawing.encode_glyphs(title)})
    insts.append({"op": "return"})

    # Band 1 (y=8..15): Title bar bottom + divider + Top buttons Line 1
    insts.append({"op": "box", "line": 1, "type": 3, "x": 2048, "y": 2048, "w": 96, "h": 8, "style": 0})
    if title:
        insts.append({"op": "inline_text", "x": x_title, "y": 0, "text": title, "glyphs": drawing.encode_glyphs(title)})
    insts.append({"op": "line", "line_type": 0, "geom": bytes.fromhex("0c00006001"), "style": 0})
    insts.append({"op": "font", "opcode": 0x10, "font_id": 0})
    _emit_quad_line(tl_lines, 0, 13, False)
    _emit_quad_line(tr_lines, 0, 13, True)
    insts.append({"op": "return"})

    # Band 2 (y=16..23): Top buttons Line 1 persistence
    insts.append({"op": "box", "line": 2, "type": 3, "x": 4096, "y": 4096, "w": 96, "h": 8, "style": 0})
    _emit_quad_line(tl_lines, 0, 13, False)
    _emit_quad_line(tr_lines, 0, 13, True)
    insts.append({"op": "return"})

    # Band 3 (y=24..31): Top buttons Line 2 (or Line 1 persistence if single-line)
    insts.append({"op": "box", "line": 3, "type": 3, "x": 6144, "y": 6144, "w": 96, "h": 8, "style": 0})
    if len(tl_lines) > 1:
        _emit_quad_line(tl_lines, 1, 24, False)
    else:
        _emit_quad_line(tl_lines, 0, 13, False)
    if len(tr_lines) > 1:
        _emit_quad_line(tr_lines, 1, 24, True)
    else:
        _emit_quad_line(tr_lines, 0, 13, True)
    insts.append({"op": "return"})

    # Band 4 (y=32..39): Top buttons Line 2 bottom + Bottom buttons Line 1
    insts.append({"op": "box", "line": 4, "type": 3, "x": 8192, "y": 8192, "w": 96, "h": 8, "style": 0})
    _emit_quad_line(tl_lines, 1, 24, False)
    _emit_quad_line(tr_lines, 1, 24, True)
    _emit_quad_line(bl_lines, 0, 35, False)
    _emit_quad_line(br_lines, 0, 35, True)
    insts.append({"op": "return"})

    # Band 5 (y=40..47): Bottom buttons Line 1 persistence + Bottom buttons Line 2 top
    insts.append({"op": "box", "line": 5, "type": 3, "x": 10240, "y": 10240, "w": 96, "h": 8, "style": 0})
    _emit_quad_line(bl_lines, 0, 35, False)
    _emit_quad_line(bl_lines, 1, 46, False)
    _emit_quad_line(br_lines, 0, 35, True)
    _emit_quad_line(br_lines, 1, 46, True)
    insts.append({"op": "return"})

    # Band 6 (y=48..55): Bottom buttons Line 2 persistence
    insts.append({"op": "box", "line": 6, "type": 3, "x": 12288, "y": 12288, "w": 96, "h": 8, "style": 0})
    _emit_quad_line(bl_lines, 1, 46, False)
    _emit_quad_line(br_lines, 1, 46, True)
    insts.append({"op": "return"})

    # Band 7 (y=56..63): Bottom buttons Line 2 bottom + Pagination footer (X OF Y)
    insts.append({"op": "box", "line": 7, "type": 3, "x": 14336, "y": 14336, "w": 96, "h": 8, "style": 0})
    _emit_quad_line(bl_lines, 1, 46, False)
    _emit_quad_line(br_lines, 1, 46, True)
    if page_count > 1:
        insts.append({"op": "font", "opcode": 0x10, "font_id": 2})
        p_cur = str(page_idx + 1)
        p_tot = str(page_count)
        insts.append({"op": "inline_text", "x": 36, "y": 56, "text": p_cur, "glyphs": drawing.encode_glyphs(p_cur)})
        insts.append({"op": "inline_text", "x": 47, "y": 56, "text": "OF", "glyphs": drawing.encode_glyphs("OF")})
        insts.append({"op": "inline_text", "x": 65, "y": 56, "text": p_tot, "glyphs": drawing.encode_glyphs(p_tot)})
    insts.append({"op": "return"})

    # End
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
    command_name_lookup: dict[tuple[int, str], int] | None = None,
    header_kreg_bytes: bytes | None = None,
) -> int:
    """Compile complete Section 06 screen records onto lay.out and return the new trailer flash pointer."""
    sec6_orig = struct.unpack_from("<I", donor_payload, 0x0C + 6 * 4)[0] - BASE
    tr_orig = int.from_bytes(donor_payload[sec6_orig + 3 + 3 * screen_idx : sec6_orig + 6 + 3 * screen_idx], "little") - BASE
    flags = donor_payload[tr_orig]

    if header_kreg_bytes is not None:
        hk_off = len(lay.out)
        lay.out.extend(header_kreg_bytes)
        hdr_kreg_flash = BASE + hk_off
    else:
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
                    if isinstance(dev, str) and dev.isdigit():
                        dev_idx = int(dev)
                    elif device_name_to_idx and isinstance(dev, str) and dev in device_name_to_idx:
                        dev_idx = device_name_to_idx[dev]
                    else:
                        dev_idx = int(dev or 0)
                    cmd = entry.get("command", "")
                    if command_name_lookup and isinstance(cmd, str) and (dev_idx, cmd) in command_name_lookup:
                        cmd_idx = command_name_lookup[(dev_idx, cmd)]
                    elif isinstance(cmd, str) and "Command_" in cmd:
                        cmd_idx = int(cmd.split("_")[-1])
                    elif isinstance(cmd, int) or (isinstance(cmd, str) and cmd.isdigit()):
                        cmd_idx = int(cmd)
                    else:
                        cmd_idx = 0
                    if command_lookup and (dev_idx, cmd_idx) in command_lookup:
                        action_obj = command_lookup[(dev_idx, cmd_idx)]
                    else:
                        action_obj = (dev_idx << 8) | cmd_idx
                        action_cat = 0x7D
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
