"""Activity triggers, macro chains, and execution graphs for Harmony 555 (Architecture 9)."""

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


def decompile_execution_chain(payload: bytes, obj_idx: int, depth: int = 5) -> list[dict]:
    """Recursively decompile an execution object into atomic commands and delays."""
    if depth <= 0:
        return []
    objs = command_index.objects(payload)
    if not (0 <= obj_idx < len(objs)):
        return []

    actions = []
    for operand, opcode in objs[obj_idx]["atoms"]:
        if opcode == COMMAND:
            actions.append({
                "type": "command",
                "device": operand >> 8,
                "command_id": operand,
            })
        elif opcode == OBJECT:
            actions.extend(decompile_execution_chain(payload, operand, depth - 1))
        elif opcode == 0x7C:  # Delay / timer wait
            actions.append({
                "type": "delay",
                "duration_ms": operand,
            })
    return actions


def activity_screen_dispatch(payload: bytes) -> dict[int, list[int]]:
    """Parse Section 14 activity switch table to find root screens per activity."""
    sec14_start = struct.unpack_from("<I", payload, 0x0C + 14 * 4)[0] - ahcm.BASE
    cnt14 = payload[sec14_start]
    activity_screens: dict[int, list[int]] = {}

    for i in range(cnt14):
        p = _u24(payload, sec14_start + 1 + 3 * i) - ahcm.BASE
        step_count = payload[p]
        screens = []
        for s in range(step_count):
            item_ptr = _u24(payload, p + 3 + s * 5) - ahcm.BASE
            if 0 <= item_ptr < len(payload):
                op, code = struct.unpack_from("<HH", payload, item_ptr + 1)
                if code == 0x7E:  # Screen navigation
                    screens.append(op)
        activity_screens[i] = screens

    return activity_screens


def inspect(payload: bytes) -> dict:
    """Return high-level activity triggers and screen contexts."""
    triggers = activity_triggers(payload)
    return {
        "activity_count": len(triggers),
        "activities": triggers,
        "screens": activity_screen_dispatch(payload),
    }
