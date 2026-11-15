"""Portable Device Representation, Signal Mapping, and Button Extraction for Harmony 555."""

from __future__ import annotations

import re
from collections import defaultdict
from . import ahcm, command_index, keys, ui_index

BASE = ahcm.BASE
OBJECT = 0x7F
COMMAND = 0x7D

_FLOW_OR_MODAL_KEYS = frozenset({"Off", "Activities", "Help", "Device", "Sound", "Picture"})
_SLOT_QUADRANTS = (
    ("top_left", 0x9E),
    ("top_right", 0x9F),
    ("bottom_left", 0xA7),
    ("bottom_right", 0xA6),
)


def _slot_quadrant_labels(slot: dict) -> dict[str, str]:
    """Deduplicate vertical text lines per [2, 2] LCD quadrant in a Section 06 slot."""
    quad_lines: dict[str, dict[int, str]] = defaultdict(dict)
    for inst in slot.get("instructions", []):
        y = inst.get("y", 0)
        x = inst.get("x", 0)
        txt = inst.get("text", "").strip()
        if not txt or not (8 <= y < 52):
            continue
        quad = ("bottom_" if y >= 30 else "top_") + ("right" if x >= 30 else "left")
        if y not in quad_lines[quad]:
            quad_lines[quad][y] = txt
    return {
        quad: " ".join(lines[y] for y in sorted(lines))
        for quad, lines in quad_lines.items()
    }


def _find_device_screens(payload: bytes, objs: list[dict]) -> dict[int, list[int]]:
    """Map each device index to its dedicated Section 06 screen index(es)."""
    def get_commands(obj_idx: int, depth: int = 3) -> list[int]:
        if depth <= 0 or not (0 <= obj_idx < len(objs)):
            return []
        atoms = objs[obj_idx]["atoms"]
        if any(op in (0x1F, 0x90) for _, op in atoms):
            return []
        cmds = []
        for operand, opcode in atoms:
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
    candidate_screens: set[int] = set()
    for s in menu_screens:
        sc = ui_index.parse_screen(payload, s)
        for sl in sc["slots"]:
            for k in sl["keys"]:
                if k["category"] == OBJECT and 0 <= k["operand"] < len(objs):
                    for atom_op, atom_code in objs[k["operand"]]["atoms"]:
                        if atom_code == 0x7E:  # Screen navigation
                            candidate_screens.add(atom_op)

    # 3. Correlate each screen with its single owning device index
    dev_screens: dict[int, list[int]] = defaultdict(list)
    for s in sorted(candidate_screens):
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

        if len(s_devices) == 1:
            dev_screens[next(iter(s_devices))].append(s)

    return dict(dev_screens)


def resolve_device_titles(payload: bytes) -> dict[int, str]:
    """Map device indices directly to their dedicated command screen titles."""
    titles: dict[int, str] = {}
    objs = command_index.objects(payload)
    dev_screens = _find_device_screens(payload, objs)

    for dev_idx, screens in dev_screens.items():
        for s in screens:
            sc = ui_index.parse_screen(payload, s)
            title = sc.get("title", "")
            if title and "(" not in title and not title.endswith(".."):
                titles[dev_idx] = title
                break

    return titles


def _detect_power_commands(
    cmd_entries: list[tuple[int, str, str]],
) -> dict[str, str]:
    """Infer power_on_cmd, power_off_cmd, and power_cmd from a device's named commands."""
    out: dict[str, str] = {}
    for _cid, name, label in cmd_entries:
        for candidate in (label, name):
            if not candidate:
                continue
            norm = re.sub(r"[\s_-]+", "", candidate).lower()
            if norm in ("pwron", "poweron", "discreteon") and "power_on_cmd" not in out:
                out["power_on_cmd"] = name
            elif norm in ("pwroff", "poweroff", "discreteoff") and "power_off_cmd" not in out:
                out["power_off_cmd"] = name
            elif norm in ("pwrtoggle", "powertoggle", "power", "pwr") and "power_cmd" not in out:
                out["power_cmd"] = name
    return out


def project_devices(payload: bytes) -> list[dict]:
    """Decompile all physical devices into portable Afterglow project device records."""
    cmd_data = command_index.inspect(payload)
    objs = command_index.objects(payload)
    dev_screens_map = _find_device_screens(payload, objs)
    titles = resolve_device_titles(payload)

    def get_commands(obj_idx: int, depth: int = 3) -> list[int]:
        if depth <= 0 or not (0 <= obj_idx < len(objs)):
            return []
        atoms = objs[obj_idx]["atoms"]
        if any(op in (0x1F, 0x90) for _, op in atoms):
            return []
        cmds = []
        for operand, opcode in atoms:
            if opcode == COMMAND:
                cmds.append(operand)
            elif opcode == OBJECT:
                cmds.extend(get_commands(operand, depth - 1))
        return cmds

    def resolve_key_cid(k: dict) -> int | None:
        if k["category"] == COMMAND:
            return k["operand"]
        if k["category"] == OBJECT:
            cmds = get_commands(k["operand"])
            if cmds:
                return cmds[0]
        return None

    total_screens = ui_index.screen_count(payload)
    cmd_labels: dict[int, str] = {}

    # Extract clean soft-button command labels by coordinate deduplication
    for s in range(total_screens):
        sc = ui_index.parse_screen(payload, s)
        for sl in sc["slots"]:
            labels = _slot_quadrant_labels(sl)
            for k in sl["keys"]:
                cid = resolve_key_cid(k)
                if cid is not None and cid not in cmd_labels:
                    key = k["key_code"]
                    lbl = None
                    if key == 0x9E:
                        lbl = labels.get("top_left")
                    elif key == 0x9F:
                        lbl = labels.get("top_right")
                    elif key == 0xA7:
                        lbl = labels.get("bottom_left")
                    elif key == 0xA6:
                        lbl = labels.get("bottom_right")
                    if lbl:
                        cmd_labels[cid] = lbl

    devices = []
    for d in cmd_data["devices"]:
        idx = d["device_index"]
        label = titles.get(idx, f"Device {idx}")

        # Pick primary dedicated screen for this device
        primary_screen: dict | None = None
        for s in dev_screens_map.get(idx, []):
            sc = ui_index.parse_screen(payload, s)
            if sc.get("title") == label or primary_screen is None:
                primary_screen = sc
                if sc.get("title") == label:
                    break

        # Extract physical hard-key bindings from the device screen's header_keys
        cmd_hard_keys: dict[int, list[str]] = defaultdict(list)
        if primary_screen is not None:
            for hk in primary_screen.get("header_keys", []):
                kname = keys.name_of(hk["key_code"])
                if not kname or kname in _FLOW_OR_MODAL_KEYS:
                    continue
                cid = resolve_key_cid(hk)
                if cid is not None and (cid >> 8) == idx:
                    if kname not in cmd_hard_keys[cid]:
                        cmd_hard_keys[cid].append(kname)

        signals: dict[str, dict] = {}
        commands: list[list] = []
        used_names: set[str] = set()
        cid_to_name: dict[int, str] = {}
        named_entries: list[tuple[int, str, str]] = []

        for ord_idx, cmd in enumerate(d["commands"]):
            cid = cmd["command_id"]
            screen_lbl = cmd_labels.get(cid, "")
            bound_keys = cmd_hard_keys.get(cid, [])

            base_name = screen_lbl or (bound_keys[0] if bound_keys else f"Command_{ord_idx}")
            base_name = " ".join(base_name.split())

            name = base_name
            if name in used_names:
                if bound_keys and bound_keys[0] not in used_names:
                    name = bound_keys[0]
                else:
                    name = f"{base_name}_{ord_idx}"
                    suffix = 2
                    while name in used_names:
                        name = f"{base_name}_{ord_idx}_{suffix}"
                        suffix += 1
            used_names.add(name)
            cid_to_name[cid] = name
            named_entries.append((cid, name, screen_lbl))

            sig = cmd["signal"]
            signals[name] = {
                "carrier_hz": sig["carrier_hz"],
                "press": sig["press"],
                "hold": sig["hold"] if sig["hold"] else None,
            }

            hardslot = (
                bound_keys[0]
                if len(bound_keys) == 1
                else (list(bound_keys) if bound_keys else None)
            )
            commands.append([name, screen_lbl or name, "", "", hardslot])

        # Build paginated device screen_buttons from the device's dedicated Section 06 slots
        screen_buttons: list[list[str] | None] = []
        if primary_screen is not None:
            for sl in primary_screen.get("slots", []):
                labels = _slot_quadrant_labels(sl)
                key_by_code = {k["key_code"]: k for k in sl.get("keys", [])}
                for quad_name, kcode in _SLOT_QUADRANTS:
                    k = key_by_code.get(kcode)
                    cid = resolve_key_cid(k) if k is not None else None
                    if cid is not None and (cid >> 8) == idx and cid in cid_to_name:
                        cmd_name = cid_to_name[cid]
                        btn_text = labels.get(quad_name) or cmd_name
                        screen_buttons.append([cmd_name, btn_text])
                    else:
                        screen_buttons.append(None)
            while screen_buttons and screen_buttons[-1] is None:
                screen_buttons.pop()

        power_fields = _detect_power_commands(named_entries)

        dev_record: dict = {
            "schema": "afterglow-project-device/1",
            "id": str(idx),
            "label": label,
            "command_count": d["command_count"],
            **power_fields,
            "signals": signals,
            "commands": commands,
        }
        if primary_screen is not None:
            dev_record["screen_buttons"] = screen_buttons

        devices.append(dev_record)

    return devices
