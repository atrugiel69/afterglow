"""Activity Decompilation, Tripartite Modes, and Key Binding Extraction for Harmony 555."""

from __future__ import annotations

import struct
from collections import defaultdict
from . import ahcm, command_index, ui_index, activity_index, keys

BASE = ahcm.BASE
OBJECT = 0x7F
COMMAND = 0x7D
ACTIVITY = 0x1F
ENTER_SCREEN = 0x7E

KEY_PICTURE = 0xA4  # Scan 36
KEY_SOUND = 0xA5    # Scan 37


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
            if code == ENTER_SCREEN:
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
    """Decompile paginated [2, 2] soft keys for a screen from its visual grid."""
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


def key_bindings_to_hard_macros(
    key_bindings: dict[str, dict],
    device_title_to_id: dict[str, str],
) -> dict[str, list]:
    """Convert declarative key_bindings into GUI-compatible hard_macros steps."""
    macros: dict[str, list] = {}
    for kname, b in key_bindings.items():
        if b.get("type") == "command":
            dev_id = device_title_to_id.get(b.get("device", ""), str(b.get("device", "0")))
            cmd_name = b.get("command", "")
            if cmd_name:
                macros[kname] = [["command", dev_id, cmd_name]]
    return macros


def decode_key_register_entries(
    raw_entries: list[dict],
    device_titles: dict[int, str],
    get_commands_fn,
    participating: set[int] | None = None,
    skip_modal_nav: bool = False,
) -> dict[str, dict]:
    """Convert raw <BHB> register entries into declarative key_bindings dict."""
    key_bindings: dict[str, dict] = {}
    for b in raw_entries:
        kcode = b["key_code"]
        cat = b["category"]
        op = b["operand"]

        if skip_modal_nav and cat == ENTER_SCREEN and kcode in (KEY_PICTURE, KEY_SOUND):
            continue

        kname = keys.name_of(kcode) or f"0x{kcode:02X}"

        if cat == ENTER_SCREEN:
            key_bindings[kname] = {"type": "screen", "screen": op}
        elif cat == COMMAND:
            dev_idx = op >> 8
            cmd_idx = op & 0xFF
            if participating is not None:
                participating.add(dev_idx)
            key_bindings[kname] = {
                "type": "command",
                "device": device_titles.get(dev_idx, f"Device {dev_idx}"),
                "command": f"Command_{cmd_idx}",
            }
        elif cat == OBJECT:
            cmds = get_commands_fn(op)
            if kcode >= 0x80 and cmds:
                if participating is not None:
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
                if cmds and participating is not None:
                    for d, _ in cmds:
                        participating.add(d)
                key_bindings[kname] = {"type": "macro", "object": op}
        else:
            key_bindings[kname] = {"type": f"cat_0x{cat:02X}", "operand": op}
    return key_bindings


def decompile_mode_screen(
    payload: bytes,
    screen_idx: int,
    device_titles: dict[int, str],
    get_commands_fn,
    participating: set[int] | None = None,
) -> dict:
    """Decompile a Sound or Picture mode screen (physical overrides + soft buttons)."""
    sc = ui_index.parse_screen(payload, screen_idx)
    mode_keys = decode_key_register_entries(
        sc.get("header_keys", []),
        device_titles,
        get_commands_fn,
        participating=participating,
        skip_modal_nav=True,
    )
    mode_soft = decompile_soft_buttons(payload, screen_idx)
    title_to_id = {title: str(idx) for idx, title in device_titles.items()}
    return {
        "screen": screen_idx,
        "title": sc.get("title") or "",
        "key_bindings": mode_keys,
        "hard_macros": key_bindings_to_hard_macros(mode_keys, title_to_id),
        "soft_buttons": mode_soft,
    }


def project_activities(payload: bytes, device_titles: dict[int, str]) -> list[dict]:
    """Decompile all user activities with participating devices, soft buttons, and tripartite modes."""
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
        sound_scr = None
        picture_scr = None
        participating: set[int] = set()

        if root_scr is not None:
            sc_root = ui_index.parse_screen(payload, root_scr)
            label = sc_root.get("title") or f"Activity {act_id}"
            soft_buttons = decompile_soft_buttons(payload, root_scr)
            for hk in sc_root.get("header_keys", []):
                if hk["category"] == ENTER_SCREEN:
                    if hk["key_code"] == KEY_SOUND:
                        sound_scr = hk["operand"]
                    elif hk["key_code"] == KEY_PICTURE:
                        picture_scr = hk["operand"]
        else:
            label = "All Off" if act_id == 10 else f"Activity {act_id}"
            soft_buttons = []

        raw_bindings = activity_index.activity_key_bindings(payload, act_id)
        key_bindings = decode_key_register_entries(
            raw_bindings,
            device_titles,
            get_commands,
            participating=participating,
            skip_modal_nav=False,
        )

        title_to_id = {title: str(idx) for idx, title in device_titles.items()}
        std_hard_macros = key_bindings_to_hard_macros(key_bindings, title_to_id)
        modes: dict[str, dict] = {
            "standard": {
                "screen": root_scr,
                "key_bindings": dict(key_bindings),
                "hard_macros": dict(std_hard_macros),
                "soft_buttons": list(soft_buttons),
            }
        }
        if sound_scr is not None:
            modes["sound"] = decompile_mode_screen(
                payload, sound_scr, device_titles, get_commands, participating=participating
            )
        if picture_scr is not None:
            modes["picture"] = decompile_mode_screen(
                payload, picture_scr, device_titles, get_commands, participating=participating
            )

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
            "sound_screen": sound_scr,
            "picture_screen": picture_scr,
            "devices": member_names,
            "key_bindings": key_bindings,
            "hard_macros": std_hard_macros,
            "soft_buttons": soft_buttons,
            "modes": modes,
        })

    return activities


def lower_activity_key_register(
    bindings_spec: dict | list,
    device_name_to_idx: dict[str, int] | None = None,
    command_lookup: dict[tuple[int, int], int] | None = None,
    command_name_lookup: dict[tuple[int, str], int] | None = None,
    preserved_entries: list[tuple[int, int, int]] | None = None,
) -> bytes:
    """Compile an activity's or mode's key bindings into a raw Architecture 9 key register."""
    entries: list[tuple[int, int, int]] = list(preserved_entries or [])

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
                cat = ENTER_SCREEN
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
                    if command_name_lookup and isinstance(cmd, str) and (dev_idx, cmd) in command_name_lookup:
                        cmd_idx = command_name_lookup[(dev_idx, cmd)]
                    elif isinstance(cmd, str) and "Command_" in cmd:
                        cmd_idx = int(cmd.split("_")[-1])
                    elif isinstance(cmd, int) or (isinstance(cmd, str) and cmd.isdigit()):
                        cmd_idx = int(cmd)
                    else:
                        cmd_idx = 0
                    if command_lookup and (dev_idx, cmd_idx) in command_lookup:
                        op = command_lookup[(dev_idx, cmd_idx)]
                        cat = OBJECT
                    else:
                        op = (dev_idx << 8) | cmd_idx
                        cat = COMMAND
                else:
                    cat = OBJECT
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
