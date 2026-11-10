"""Activity Decompilation, Soft-Button Pagination, and Key Binding Extraction for Harmony 555."""

from __future__ import annotations

import struct
from collections import defaultdict
from . import ahcm, command_index, ui_index, activity_index, keys

BASE = ahcm.BASE
OBJECT = 0x7F
COMMAND = 0x7D
ACTIVITY = 0x1F


def resolve_activity_catalog(payload: bytes) -> dict[int, dict]:
    """Resolve activity IDs, triggers, and root screens from Section 14 and trigger objects."""
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

    act_ids: list[int] = []
    if cnt14 > 6:
        p6 = ptrs14[6]
        c6 = payload[p6 + 1]
        for i in range(c6):
            target = int.from_bytes(payload[p6 + 4 + i * 5 : p6 + 7 + i * 5], "little") - BASE
            op, code = struct.unpack_from("<HH", payload, target + 1)
            act_ids.append(op & 0xFF if code == ACTIVITY else op)

    screens: list[int] = []
    if cnt14 > 9:
        p9 = ptrs14[9]
        c9 = payload[p9 + 1]
        for s in range(c9):
            target = int.from_bytes(payload[p9 + 4 + s * 5 : p9 + 7 + s * 5], "little") - BASE
            op, code = struct.unpack_from("<HH", payload, target + 1)
            if code == 0x7E:  # Screen navigation
                screens.append(op)

    out = {}
    for aid, sid in zip(act_ids, screens):
        out[aid] = {
            "trigger_object": triggers.get(aid),
            "root_screen": sid,
        }

    for aid, trig_obj in triggers.items():
        if aid not in out:
            out[aid] = {
                "trigger_object": trig_obj,
                "root_screen": None,
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

        buttons.extend([
            labels.get("top_left"),
            labels.get("top_right"),
            labels.get("bottom_left"),
            labels.get("bottom_right"),
        ])

    return buttons


def project_activities(payload: bytes, device_titles: dict[int, str]) -> list[dict]:
    """Decompile all user activities with participating devices, soft buttons, and physical key bindings."""
    catalog = resolve_activity_catalog(payload)
    total_screens = ui_index.screen_count(payload)
    objs = command_index.objects(payload)

    def get_commands(obj_idx: int, depth: int = 3) -> list[tuple[int, int]]:
        if depth <= 0 or not (0 <= obj_idx < len(objs)):
            return []
        cmds = []
        for operand, opcode in objs[obj_idx]["atoms"]:
            if opcode == COMMAND:
                cmds.append((operand >> 8, operand & 0xFF))
            elif opcode == OBJECT:
                cmds.extend(get_commands(operand, depth - 1))
        return cmds

    activities = []
    for act_id, info in sorted(catalog.items()):
        root_scr = info["root_screen"]
        if root_scr is not None:
            sc_root = ui_index.parse_screen(payload, root_scr)
            label = sc_root.get("title") or f"Activity {act_id}"
            soft_buttons = decompile_soft_buttons(payload, root_scr)
        else:
            label = "All Off" if act_id == 10 else f"Activity {act_id}"
            soft_buttons = []

        participating: set[int] = set()

        raw_bindings = activity_index.activity_key_bindings(payload, act_id)
        key_bindings: dict[str, dict] = {}
        for b in raw_bindings:
            kcode = b["key_code"]
            kname = keys.name_of(kcode) or f"0x{kcode:02X}"
            cat = b["category"]
            op = b["operand"]

            if cat == 0x7E:
                key_bindings[kname] = {"type": "screen", "screen": op}
            elif cat == 0x7F:
                cmds = get_commands(op)
                if kcode >= 0x80 and cmds:
                    for d, _ in cmds:
                        participating.add(d)
                    dev_idx, cmd_idx = cmds[0]
                    key_bindings[kname] = {
                        "type": "command",
                        "device": device_titles.get(dev_idx, f"Device {dev_idx}"),
                        "command": f"Command_{cmd_idx}",
                        "object": op,
                    }
                else:
                    if cmds:
                        for d, _ in cmds:
                            participating.add(d)
                    key_bindings[kname] = {"type": "macro", "object": op}
            else:
                key_bindings[kname] = {"type": f"cat_0x{cat:02X}", "operand": op}

        if root_scr is not None:
            for s in range(total_screens):
                sc = ui_index.parse_screen(payload, s)
                t = sc.get("title", "")
                if t == label or t.startswith(label + " (") or t.startswith(label + ".."):
                    for k in sc["header_keys"]:
                        if k["category"] == COMMAND:
                            participating.add(k["operand"] >> 8)
                        elif k["category"] == OBJECT:
                            for d, _ in get_commands(k["operand"]):
                                participating.add(d)

                    for sl in sc["slots"]:
                        for k in sl["keys"]:
                            if k["category"] == COMMAND:
                                participating.add(k["operand"] >> 8)
                            elif k["category"] == OBJECT:
                                for d, _ in get_commands(k["operand"]):
                                    participating.add(d)

        member_names = [device_titles.get(d, f"Device {d}") for d in sorted(participating)]

        activities.append({
            "id": str(act_id),
            "label": label,
            "trigger_object": info["trigger_object"],
            "root_screen": root_scr,
            "devices": member_names,
            "key_bindings": key_bindings,
            "soft_buttons": soft_buttons,
        })

    return activities


def lower_activity_key_register(
    bindings_spec: dict | list,
    device_name_to_idx: dict[str, int] | None = None,
    command_lookup: dict[tuple[int, int], int] | None = None,
) -> bytes:
    """Compile an activity's key bindings into a raw Architecture 9 key register."""
    entries: list[tuple[int, int, int]] = []

    if isinstance(bindings_spec, list):
        for b in bindings_spec:
            kcode = b.get("key_code")
            if kcode is None:
                kname = b.get("key", "")
                kcode = keys.by_name(kname)
                if kcode is None:
                    kcode = int(kname, 16) if kname.startswith(("0x", "0X")) else int(kname)
            entries.append((kcode, b["operand"], b["category"]))

    elif isinstance(bindings_spec, dict):
        for kname, b in bindings_spec.items():
            kcode = keys.by_name(kname)
            if kcode is None:
                kcode = int(kname, 16) if kname.startswith(("0x", "0X")) else int(kname)

            btype = b.get("type")
            if btype == "screen":
                op = b.get("screen", 0)
                cat = 0x7E
            elif btype in ("macro", "command"):
                op = b.get("object")
                if op is None:
                    dev = b.get("device")
                    dev_idx = (
                        device_name_to_idx.get(dev, 0)
                        if (device_name_to_idx and isinstance(dev, str))
                        else int(dev or 0)
                    )
                    cmd = b.get("command", "")
                    cmd_idx = (
                        int(cmd.split("_")[-1])
                        if isinstance(cmd, str) and "Command_" in cmd
                        else int(cmd or 0)
                    )
                    op = (
                        command_lookup.get((dev_idx, cmd_idx), 0)
                        if command_lookup
                        else ((dev_idx << 8) | cmd_idx)
                    )
                cat = 0x7F
            elif btype and btype.startswith("cat_0x"):
                cat = int(btype.split("cat_0x")[-1], 16)
                op = b.get("operand", 0)
            else:
                op = b.get("operand", 0)
                cat = b.get("category", 0x00)

            entries.append((kcode, op, cat))

    out = bytearray([len(entries)])
    for kcode, op, cat in entries:
        out.extend(struct.pack("<BHB", kcode, op, cat))
    return bytes(out)
