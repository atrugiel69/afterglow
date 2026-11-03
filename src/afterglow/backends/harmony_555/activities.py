"""Activity Decompilation, Soft-Button Pagination, and Tripartite Mode Router for Harmony 555."""

from __future__ import annotations

import struct
from collections import defaultdict
from . import ahcm, command_index, ui_index

BASE = ahcm.BASE
OBJECT = 0x7F
COMMAND = 0x7D
ACTIVITY = 0x1F


def resolve_activity_catalog(payload: bytes) -> dict[int, dict]:
    """Resolve activity triggers and find root screens declared in Section 14."""
    objs = command_index.objects(payload)
    triggers: dict[int, int] = {}

    for obj in objs:
        for operand, opcode in obj["atoms"]:
            if opcode == ACTIVITY and (operand >> 8) == 0xFF:
                triggers[operand & 0xFF] = obj["index"]

    sec14_start = struct.unpack_from("<I", payload, 0x0C + 14 * 4)[0] - BASE
    cnt14 = payload[sec14_start]
    ptrs14 = [
        int.from_bytes(payload[sec14_start + 1 + 3 * i : sec14_start + 4 + 3 * i], "little") - BASE
        for i in range(cnt14)
    ]

    sec14_screens: list[int] = []
    if cnt14 > 9:
        p9 = ptrs14[9]
        c9 = payload[p9 + 1]
        for s in range(c9):
            target = int.from_bytes(payload[p9 + 4 + s * 5 : p9 + 7 + s * 5], "little") - BASE
            op, code = struct.unpack_from("<HH", payload, target + 1)
            if code == 0x7E:  # Screen navigation
                sec14_screens.append(op)

    out = {}
    for act_id, trig_obj in triggers.items():
        out[act_id] = {
            "trigger_object": trig_obj,
            "candidate_screens": sec14_screens,
        }
    return out


def decompile_soft_buttons(payload: bytes, screen_idx: int) -> list[str | None]:
    """Decompile paginated [2, 2] soft keys for an activity screen from its visual grid."""
    sc = ui_index.parse_screen(payload, screen_idx)
    buttons: list[str | None] = []

    for sl in sc["slots"]:
        quad_lines: dict[str, dict[int, str]] = defaultdict(dict)
        for inst in sl["instructions"]:
            y = inst.get("y", 0)
            x = inst.get("x", 0)
            txt = inst.get("text", "").strip()
            if not txt or not (8 <= y < 52):
                continue
            quad = ("bottom_" if y >= 30 else "top_") + ("right" if x >= 30 else "left")
            if y not in quad_lines[quad]:
                quad_lines[quad][y] = txt

        labels = {
            quad: " ".join(lines[y] for y in sorted(lines))
            for quad, lines in quad_lines.items()
        }

        # Bezel buttons in standard [2, 2] order: Top-Left, Top-Right, Bottom-Left, Bottom-Right
        buttons.extend([
            labels.get("top_left"),
            labels.get("top_right"),
            labels.get("bottom_left"),
            labels.get("bottom_right"),
        ])

    return buttons


def project_activities(payload: bytes, device_titles: dict[int, str]) -> list[dict]:
    """Decompile all user activities with participating devices, soft buttons, and tripartite modes."""
    catalog = resolve_activity_catalog(payload)
    total_screens = ui_index.screen_count(payload)
    objs = command_index.objects(payload)

    def get_commands(obj_idx: int, depth: int = 3) -> list[int]:
        if depth <= 0 or not (0 <= obj_idx < len(objs)):
            return []
        cmds = []
        for operand, opcode in objs[obj_idx]["atoms"]:
            if opcode == COMMAND:
                cmds.append(operand)
            elif opcode == OBJECT:
                cmds.extend(get_commands(operand, depth - 1))
        return cmds

    # Build title-to-activity lookup from Screen 52 (Activities selection menu)
    act_labels: dict[int, str] = {}
    sc52 = ui_index.parse_screen(payload, 52)

    for slot_idx, sl in enumerate(sc52["slots"]):
        quad_lines: dict[str, dict[int, str]] = defaultdict(dict)
        for inst in sl["instructions"]:
            y, x = inst.get("y", 0), inst.get("x", 0)
            txt = inst.get("text", "").strip()
            if txt and (8 <= y < 52):
                quad = ("bottom_" if y >= 30 else "top_") + ("right" if x >= 30 else "left")
                if y not in quad_lines[quad]:
                    quad_lines[quad][y] = txt

        labels = {q: " ".join(lines[y] for y in sorted(lines)) for q, lines in quad_lines.items()}
        for k in sl["keys"]:
            if k["category"] == OBJECT and 0 <= k["operand"] < len(objs):
                for atom_op, atom_code in objs[k["operand"]]["atoms"]:
                    if atom_code == ACTIVITY and (atom_op >> 8) == 0xFF:
                        act_id = atom_op & 0xFF
                        key = k["key_code"]
                        lbl = None
                        if slot_idx == 0:
                            if key == 0x9E: lbl = labels.get("top_left")
                            elif key == 0x9F: lbl = labels.get("top_right")
                            elif key == 0xA7: lbl = labels.get("bottom_left")
                            elif key == 0xA6: lbl = labels.get("bottom_right")
                        else:
                            # 2-item row in Slot 1
                            if key in (0x9E, 0xA7):
                                lbl = labels.get("top_left") or labels.get("bottom_left")
                            elif key in (0x9F, 0xA6):
                                lbl = labels.get("top_right") or labels.get("bottom_right")
                        if lbl and act_id not in act_labels:
                            act_labels[act_id] = lbl

    act_labels[10] = "All Off"

    activities = []
    for act_id, info in sorted(catalog.items()):
        label = act_labels.get(act_id, f"Activity {act_id}")

        # Find the root screen whose title exactly matches this activity's label
        root_scr = None
        for cand_scr in info.get("candidate_screens", []):
            sc = ui_index.parse_screen(payload, cand_scr)
            if sc.get("title") == label:
                root_scr = cand_scr
                break

        participating: set[int] = set()
        soft_buttons = []

        if root_scr is not None:
            soft_buttons = decompile_soft_buttons(payload, root_scr)

        # Discover participating devices across this activity's Standard, Sound, and Picture screens
        for s in range(total_screens):
            sc = ui_index.parse_screen(payload, s)
            t = sc.get("title", "")
            if t == label or t.startswith(label + " (") or t.startswith(label + ".."):
                for k in sc["header_keys"]:
                    if k["category"] == COMMAND:
                        participating.add(k["operand"] >> 8)
                    elif k["category"] == OBJECT:
                        for c in get_commands(k["operand"]):
                            participating.add(c >> 8)

                for sl in sc["slots"]:
                    for k in sl["keys"]:
                        if k["category"] == COMMAND:
                            participating.add(k["operand"] >> 8)
                        elif k["category"] == OBJECT:
                            for c in get_commands(k["operand"]):
                                participating.add(c >> 8)

        member_names = [device_titles.get(d, f"Device {d}") for d in sorted(participating)]

        activities.append({
            "id": str(act_id),
            "label": label,
            "trigger_object": info["trigger_object"],
            "root_screen": root_scr,
            "devices": member_names,
            "soft_buttons": soft_buttons,
        })

    return activities
