"""Section 06 Screen Engine, Slot Parser, and Key Binding Index for Harmony 555."""

from __future__ import annotations

import struct
from . import ahcm, drawing

BASE = ahcm.BASE
OBJECT = 0x7F
COMMAND = 0x7D


def _u24(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 3], "little")


def screen_count(payload: bytes) -> int:
    """Total screen count declared at start of Section 06."""
    sec6 = struct.unpack_from("<I", payload, 0x0C + 6 * 4)[0] - BASE
    return struct.unpack_from("<H", payload, sec6)[0]


def parse_key_register(payload: bytes, offset: int) -> list[dict]:
    """Parse an Architecture 9 key register binding hardware scan codes to execution objects."""
    if not (0 <= offset < len(payload)):
        return []
    cnt = payload[offset]
    at, sz = (offset + 2, 5) if cnt == 0 else (offset + 1, 4)
    cnt = payload[offset + 1] if cnt == 0 else cnt

    entries = []
    for _ in range(cnt):
        if at + sz > len(payload):
            break
        raw = payload[at : at + sz]
        key_code = raw[0]
        operand, category = struct.unpack_from("<HB", raw, 2 if sz == 5 else 1)
        entries.append({
            "key_code": key_code,
            "operand": operand,
            "category": category,
        })
        at += sz
    return entries


def parse_screen(payload: bytes, screen_idx: int) -> dict:
    """Parse a single Section 06 screen record: header, physical soft slots, and ASTs."""
    sec6 = struct.unpack_from("<I", payload, 0x0C + 6 * 4)[0] - BASE
    tr = _u24(payload, sec6 + 3 + 3 * screen_idx) - BASE
    if not (0 <= tr < len(payload) - 6):
        return {"screen_idx": screen_idx, "slots": [], "header_keys": []}

    flags = payload[tr]
    h_ptr = _u24(payload, tr + 1) - BASE
    slot_cnt = struct.unpack_from("<H", payload, tr + 4)[0]

    header_keys = parse_key_register(payload, h_ptr) if 0 <= h_ptr < len(payload) else []

    slots = []
    title = ""
    for sl in range(slot_cnt):
        s_off = _u24(payload, tr + 6 + 3 * sl) - BASE
        kreg_off = _u24(payload, s_off) - BASE
        prog_off = _u24(payload, s_off + 3) - BASE

        keys = parse_key_register(payload, kreg_off)
        instructions, _ = drawing.disassemble(payload, prog_off)

        # Extract title from (y == 0) instruction if present
        for inst in instructions:
            if inst.get("y") == 0 and inst.get("text"):
                t = inst["text"]
                if t and t != "Devices" and not title:
                    title = t

        slots.append({
            "slot_idx": sl,
            "kreg_offset": kreg_off,
            "prog_offset": prog_off,
            "keys": keys,
            "instructions": instructions,
        })

    return {
        "screen_idx": screen_idx,
        "flags": flags,
        "title": title,
        "header_keys": header_keys,
        "slots": slots,
    }
