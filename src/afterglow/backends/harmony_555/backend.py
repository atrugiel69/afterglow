"""Harmony 555 (Architecture 9 / Mocha Grande) backend for Afterglow."""

from __future__ import annotations

import re
import struct
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE = 0x020000

# Verified Architecture 9 / Mocha Grande Glyph Map
GLYPHS = {
    0x01: "H", 0x02: "y", 0x03: "S", 0x04: "u", 0x05: "a", 0x06: "T",
    0x07: "e", 0x08: "r", 0x09: "m", 0x0A: "i", 0x0B: "n", 0x0C: "t",
    0x0D: " ", 0x10: "S", 0x12: "C", 0x14: "D", 0x18: "u", 0x19: "c",
    0x1A: "l", 0x1B: "0", 0x1D: "o", 0x1F: "b", 0x20: "l", 0x22: "x",
    0x28: "z", 0x2A: "R", 0x2B: "P", 0x2C: "h", 0x2E: "M", 0x2F: "q",
    0x31: "v", 0x32: "V", 0x33: "3", 0x34: "0", 0x35: "1", 0x37: "Y",
    0x38: "-", 0x39: "A", 0x3B: "X",
}


def _u24(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 3], "little")


def _decode_glyphs(raw: bytes) -> str:
    return "".join(GLYPHS.get(b, "") for b in raw).strip()


def _parse_slot_text(payload: bytes, prog_off: int) -> tuple[str, str]:
    """Extract Line 1 and Line 2 text from a 2-line menu button drawing program."""
    left_lines: list[tuple[int, str]] = []
    right_lines: list[tuple[int, str]] = []
    cur = prog_off
    end = min(prog_off + 140, len(payload))

    while cur < end:
        op = payload[cur]
        if op == 0x00:
            break
        elif op == 0x16:  # Box container (12 bytes)
            cur += 12
        elif op == 0x03:  # Separator line (10 bytes)
            cur += 10
        elif op in (0x10, 0x11):  # Attribute / Atom
            cur += 2
        elif op == 0x17:  # Return / Box end
            cur += 1
        elif op == 0x04:  # TEXT: x, y, target pointer
            x, y = payload[cur + 1], payload[cur + 2]
            t = _u24(payload, cur + 3) - BASE
            if 0 <= t < len(payload):
                null_pos = payload.find(b"\x00", t)
                raw = payload[t : null_pos if null_pos != -1 else t + 12]
                text = _decode_glyphs(raw)
                if y >= 8 and text:
                    (right_lines if x >= 30 else left_lines).append((y, text))
            cur += 6
        elif op == 0x05:  # INLINE TEXT: x, y, null-terminated glyphs
            x, y = payload[cur + 1], payload[cur + 2]
            start_g = cur + 3
            null_pos = payload.find(b"\x00", start_g)
            if null_pos != -1 and null_pos - start_g < 32:
                text = _decode_glyphs(payload[start_g:null_pos])
                if y >= 8 and text:
                    (right_lines if x >= 30 else left_lines).append((y, text))
                cur = null_pos + 1
            else:
                cur += 4
        else:
            cur += 1

    def _join_lines(lines: list[tuple[int, str]]) -> str:
        lines.sort(key=lambda item: item[0])
        unique: list[str] = []
        for _, txt in lines:
            if not unique or unique[-1] != txt:
                unique.append(txt)
        return " ".join(unique)

    return _join_lines(left_lines), _join_lines(right_lines)


def extract_device_names(payload: bytes) -> dict[int, str]:
    """Resolve device names using Tier 1 (SmartState) and Tier 2 (Section 06 UI decompile)."""
    names: dict[int, str] = {}

    # Total device count from Section 04
    sec4 = _u24(payload, 0x0C + 4 * 4) - BASE
    dev_count = payload[sec4] if 0 <= sec4 < len(payload) else 10

    # Tier 1: Section 00 SmartState Variables
    sec0 = _u24(payload, 0x0C) - BASE
    sec0_end = _u24(payload, 0x10) - BASE
    pattern = re.compile(rb"([A-Za-z0-9_-]+)_(Power|Input)")
    sec0_labels = list(
        dict.fromkeys(
            m.group(1).decode("ascii", errors="ignore").replace("_", " ")
            for m in pattern.finditer(payload[sec0:sec0_end])
        )
    )
    for idx, name in enumerate(sec0_labels):
        names[idx] = name

    # Tier 2: Decompile Section 06 Menu Buttons for Stateless Devices
    sec10 = _u24(payload, 0x0C + 10 * 4) - BASE
    cnt10 = struct.unpack_from("<H", payload, sec10)[0]
    nav_to_screen: dict[int, int] = {}
    cmd_to_device: dict[int, int] = {}

    for i in range(cnt10):
        ptr = _u24(payload, sec10 + 2 + 3 * i) - BASE
        if 0 <= ptr < len(payload):
            atoms = payload[ptr]
            for a in range(atoms):
                op, code = struct.unpack_from("<HB", payload, ptr + 1 + 3 * a)
                if code == 0x7E:
                    nav_to_screen[i] = op
                elif code == 0x7D:
                    cmd_to_device[i] = op >> 8

    sec6 = _u24(payload, 0x0C + 6 * 4) - BASE
    cnt6 = struct.unpack_from("<H", payload, sec6)[0]
    screen_devices: dict[int, set[int]] = defaultdict(set)

    for s in range(cnt6):
        tr = _u24(payload, sec6 + 3 + 3 * s) - BASE
        if not (0 <= tr < len(payload) - 6):
            continue
        h_ptr = _u24(payload, tr + 1) - BASE
        if 0 <= h_ptr < len(payload):
            cnt = payload[h_ptr]
            at, sz = (h_ptr + 2, 5) if cnt == 0 else (h_ptr + 1, 4)
            cnt = payload[h_ptr + 1] if cnt == 0 else cnt
            for _ in range(cnt):
                if at + sz > len(payload):
                    break
                op, cat = struct.unpack_from("<HB", payload, at + (2 if sz == 5 else 1))
                if cat == 0x7F and op in cmd_to_device:
                    screen_devices[s].add(cmd_to_device[op])
                at += sz

    # Trace menu screens to resolve stateless device buttons
    for s in range(cnt6):
        tr = _u24(payload, sec6 + 3 + 3 * s) - BASE
        if not (0 <= tr < len(payload) - 6):
            continue
        slot_cnt = struct.unpack_from("<H", payload, tr + 4)[0]
        for slot_idx in range(slot_cnt):
            s_off = _u24(payload, tr + 6 + 3 * slot_idx) - BASE
            kreg = _u24(payload, s_off) - BASE
            prog = _u24(payload, s_off + 3) - BASE

            row_devs: list[int] = []
            if 0 <= kreg < len(payload):
                cnt = payload[kreg]
                at, sz = (kreg + 2, 5) if cnt == 0 else (kreg + 1, 4)
                cnt = payload[kreg + 1] if cnt == 0 else cnt
                for _ in range(cnt):
                    if at + sz > len(payload):
                        break
                    op, cat = struct.unpack_from("<HB", payload, at + (2 if sz == 5 else 1))
                    if cat == 0x7F and op in nav_to_screen:
                        devs = screen_devices.get(nav_to_screen[op])
                        if devs:
                            row_devs.append(list(devs)[0])
                    at += sz

            left_txt, right_txt = _parse_slot_text(payload, prog)
            if len(row_devs) >= 1 and left_txt and (row_devs[0] not in names or names[row_devs[0]].startswith("Device")):
                names[row_devs[0]] = left_txt
            if len(row_devs) >= 2 and right_txt and (row_devs[1] not in names or names[row_devs[1]].startswith("Device")):
                names[row_devs[1]] = right_txt

    return {d: names.get(d, f"Device {d}") for d in range(dev_count)}
