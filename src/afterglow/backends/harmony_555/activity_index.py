"""Activity triggers and context resolution for Harmony 555 (Architecture 9)."""

from __future__ import annotations

import struct
from . import ahcm, command_index

OBJECT = 0x7F
COMMAND = 0x7D
ACTIVITY = 0x1F


def _u24(payload: bytes, offset: int) -> int:
    return int.from_bytes(payload[offset : offset + 3], "little")


def activity_triggers(payload: bytes) -> dict[int, int]:
    """Find all Section 10 objects that launch an activity (opcode 0x1F, operand 0xFF00 | id)."""
    objs = command_index.objects(payload)
    triggers = {}
    for obj in objs:
        for operand, opcode in obj["atoms"]:
            if opcode == ACTIVITY and (operand >> 8) == 0xFF:
                triggers[operand & 0xFF] = obj["index"]
    return triggers


def screen_contexts(payload: bytes) -> list[dict]:
    """Extract Section 06 screen keymap contexts and bound buttons."""
    start = struct.unpack_from("<I", payload, 0x0C + 6 * 4)[0] - ahcm.BASE
    count = struct.unpack_from("<H", payload, start)[0]

    contexts = []
    for s in range(count):
        pos = start + 3 + 3 * s
        ptr = _u24(payload, pos) - ahcm.BASE
        if not (0 <= ptr < len(payload) - 10):
            continue
        btn_count = payload[ptr + 9]
        buttons = []
        for b in range(btn_count):
            off = ptr + 10 + b * 4
            if off + 4 > len(payload):
                break
            key_code = payload[off]
            operand, category = struct.unpack_from("<HB", payload, off + 1)
            buttons.append({
                "key_code": key_code,
                "operand": operand,
                "category": category,
            })
        contexts.append({
            "slot": s,
            "offset": ptr,
            "button_count": len(buttons),
            "buttons": buttons,
        })
    return contexts


def inspect(payload: bytes) -> dict:
    """Return high-level activity triggers and screen context counts."""
    triggers = activity_triggers(payload)
    ctxs = screen_contexts(payload)
    return {
        "activity_count": len(triggers),
        "activities": triggers,
        "context_count": len(ctxs),
    }
