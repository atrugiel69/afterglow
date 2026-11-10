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


def activity_screen_dispatch(payload: bytes) -> dict[int, dict]:
    """Parse Section 14 to correlate Activity IDs with their root screens."""
    sec14_start = struct.unpack_from("<I", payload, 0x0C + 14 * 4)[0] - ahcm.BASE
    cnt14 = payload[sec14_start]

    entries = []
    for i in range(cnt14):
        p = _u24(payload, sec14_start + 1 + 3 * i) - ahcm.BASE
        step_count = payload[p + 1]
        atoms = []
        for s in range(step_count):
            item_ptr = _u24(payload, p + 4 + s * 5) - ahcm.BASE
            if 0 <= item_ptr + 3 <= len(payload):
                op, code = struct.unpack_from("<HB", payload, item_ptr + 1)
                atoms.append((code, op))
        entries.append(atoms)

    dispatch = {}
    if len(entries) > 9:
        act_atoms = entries[6]
        scr_atoms = entries[9]
        for (a_code, a_op), (s_code, s_op) in zip(act_atoms, scr_atoms):
            if a_code == 0x1F and s_code == 0x7E:
                act_id = a_op & 0xFF
                title = ui_index.parse_screen(payload, s_op).get("title", "")
                dispatch[act_id] = {
                    "activity_id": act_id,
                    "root_screen": s_op,
                    "title": title,
                }
    return dispatch


def activity_key_bindings(payload: bytes, activity_id: int) -> list[dict]:
    """Parse Section 9 to extract physical key bindings for a given Activity ID."""
    sec9_start = struct.unpack_from("<I", payload, 0x0C + 9 * 4)[0] - ahcm.BASE
    cnt = payload[sec9_start]
    if not (0 <= activity_id < cnt):
        return []

    p = _u24(payload, sec9_start + 1 + activity_id * 3) - ahcm.BASE
    if not (0 <= p < len(payload)):
        return []

    key_count = payload[p]
    pos = p + 1
    bindings = []
    for _ in range(key_count):
        if pos + 4 > len(payload):
            break
        kcode, op, code = struct.unpack_from("<BHB", payload, pos)
        pos += 4
        bindings.append({
            "key_code": kcode,
            "operand": op,
            "category": code,
        })
    return bindings
