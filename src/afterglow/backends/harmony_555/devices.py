"""Portable Device Representation, Signal Mapping, and Button Extraction for Harmony 555."""

from __future__ import annotations

import struct
from collections import defaultdict
from . import ahcm, command_index, ui_index

BASE = ahcm.BASE
OBJECT = 0x7F
COMMAND = 0x7D


def resolve_device_titles(payload: bytes) -> dict[int, str]:
    """Map device indices directly to their dedicated command screen titles."""
    titles: dict[int, str] = {}
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

    total_screens = ui_index.screen_count(payload)

    # 1. Identify root Device Menu carousel screens (Screen titled "Devices")
    menu_screens: list[int] = []
    for s in range(total_screens):
        sc = ui_index.parse_screen(payload, s)
        for sl in sc["slots"]:
            for inst in sl["instructions"]:
                if inst.get("y") == 0 and inst.get("text") == "Devices":
                    menu_screens.append(s)
                    break

    # 2. Trace soft buttons to dedicated command screens
    device_screens: set[int] = set()
    for s in menu_screens:
        sc = ui_index.parse_screen(payload, s)
        for sl in sc["slots"]:
            for k in sl["keys"]:
                if k["category"] == OBJECT and 0 <= k["operand"] < len(objs):
                    for atom_op, atom_code in objs[k["operand"]]["atoms"]:
                        if atom_code == 0x7E:  # Screen navigation
                            device_screens.add(atom_op)

    # 3. Read title and owning device index from each screen
    for s in device_screens:
        sc = ui_index.parse_screen(payload, s)
        s_devices: set[int] = set()

        for k in sc["header_keys"]:
            if k["category"] == COMMAND:
                s_devices.add(k["operand"] >> 8)
            elif k["category"] == OBJECT:
                for c in get_commands(k["operand"]):
                    s_devices.add(c >> 8)

        for sl in sc["slots"]:
            for k in sl["keys"]:
                if k["category"] == COMMAND:
                    s_devices.add(k["operand"] >> 8)
                elif k["category"] == OBJECT:
                    for c in get_commands(k["operand"]):
                        s_devices.add(c >> 8)

        if len(s_devices) == 1 and sc["title"] and "(" not in sc["title"] and not sc["title"].endswith(".."):
            titles[next(iter(s_devices))] = sc["title"]

    return titles


def project_devices(payload: bytes) -> list[dict]:
    """Decompile all physical devices into portable Afterglow project device records."""
    cmd_data = command_index.inspect(payload)
    titles = resolve_device_titles(payload)
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

    total_screens = ui_index.screen_count(payload)
    cmd_labels: dict[int, str] = {}

    # Extract clean soft-button command labels by coordinate deduplication
    for s in range(total_screens):
        sc = ui_index.parse_screen(payload, s)
        for sl in sc["slots"]:
            # Deduplicate text by exact vertical row line (e.g. Line 1: y=13/35, Line 2: y=24/46)
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

            # Join Line 1 and Line 2 cleanly in vertical order
            labels = {
                quad: " ".join(lines[y] for y in sorted(lines))
                for quad, lines in quad_lines.items()
            }

            for k in sl["keys"]:
                cid = None
                if k["category"] == COMMAND:
                    cid = k["operand"]
                elif k["category"] == OBJECT:
                    cmds = get_commands(k["operand"])
                    if cmds:
                        cid = cmds[0]

                if cid is not None and cid not in cmd_labels:
                    key = k["key_code"]
                    lbl = None
                    if key == 0x9E: lbl = labels.get("top_left")
                    elif key == 0x9F: lbl = labels.get("top_right")
                    elif key == 0xA7: lbl = labels.get("bottom_left")
                    elif key == 0xA6: lbl = labels.get("bottom_right")
                    if lbl:
                        cmd_labels[cid] = lbl

    devices = []
    for d in cmd_data["devices"]:
        idx = d["device_index"]
        label = titles.get(idx, f"Device {idx}")

        signals: dict[str, dict] = {}
        commands: list[list] = []

        for ord_idx, cmd in enumerate(d["commands"]):
            cid = cmd["command_id"]
            name = cmd_labels.get(cid, f"Command_{ord_idx}")
            sig = cmd["signal"]

            signals[name] = {
                "carrier_hz": sig["carrier_hz"],
                "press": sig["press"],
                "hold": sig["hold"] if sig["hold"] else None,
            }
            commands.append([name, name, "", "", None])

        devices.append({
            "schema": "afterglow-project-device/1",
            "id": str(idx),
            "label": label,
            "command_count": d["command_count"],
            "signals": signals,
            "commands": commands,
        })

    return devices
