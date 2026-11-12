"""The Harmony 555 backend: public Afterglow contract implementation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from ... import payloads
from ..contracts import BuildResult, ImportResult
from . import ahcm, importing

SCREEN_GAPS = True


def validate_payload(payload: bytes, _profile=None) -> dict:
    """Validate AHCM container magic, 20-section table, end vector, and XOR checksum."""
    return ahcm.validate(payload)


def import_project(source, profile=None, context=None) -> ImportResult:
    """Read an AHCM binary image into an Afterglow project structure."""
    carrier = payloads.get(getattr(profile, "payload", "blob")) if profile else None
    if isinstance(source, (str, Path)):
        payload = Path(source).read_bytes()
    else:
        payload = bytes(source)

    if carrier:
        payload = carrier.build(source)

    validation = validate_payload(payload)
    project = importing.read_project(payload)

    return ImportResult(
        project=project,
        preservation={
            "mode": "opaque-native-payload",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "validation": validation,
        },
        warnings=(),
        rejected={},
    )


def _soft_labels(soft_list: list) -> list[str | None]:
    return [
        e if (e is None or isinstance(e, str))
        else (e[0] if isinstance(e, (list, tuple)) else e.get("label"))
        for e in soft_list
    ]


def _soft_has_actions(soft_list: list) -> bool:
    return any(
        isinstance(e, dict) and any(k in e for k in ("operand", "object", "command", "category", "device"))
        for e in soft_list
    )


def _merge_gui_hard_macros(
    base_bindings: dict,
    hard_macros: dict | None,
    orig_hard_macros: dict | None,
    dev_id_to_title: dict[str, str],
) -> dict:
    """Apply GUI hard_macros mutations onto key_bindings without breaking unmodified entries."""
    if hard_macros is None or hard_macros == (orig_hard_macros or {}):
        return base_bindings
    merged = dict(base_bindings)
    # Remove keys cleared in GUI
    for kname in list(merged.keys()):
        if kname in (orig_hard_macros or {}) and kname not in hard_macros:
            del merged[kname]
    # Add or update keys changed in GUI
    for kname, steps in hard_macros.items():
        if not steps:
            merged.pop(kname, None)
            continue
        if (orig_hard_macros or {}).get(kname) == steps and kname in merged:
            continue
        step = steps[0]
        if isinstance(step, (list, tuple)) and len(step) >= 3 and step[0] == "command":
            dev_id = str(step[1])
            cmd_name = str(step[2])
            merged[kname] = {
                "type": "command",
                "device": dev_id_to_title.get(dev_id, dev_id),
                "command": cmd_name,
            }
    return merged


def build_project(project: dict, profile=None, context=None) -> BuildResult:
    """Outbound compiler: synthesize an AHCM image from an Afterglow project."""
    from . import ahcm, assemble, layout, scaffold, activities as act_mod, ui_index, command_index

    if profile is not None and hasattr(profile, "require_buildable"):
        profile.require_buildable()

    settings = project.get("settings") or {}
    scaffold_dir = (
        context.find_scaffold(profile.id, context.root)
        if (context and profile)
        else None
    )

    if scaffold_dir and (Path(scaffold_dir) / "payload.bin").is_file():
        donor_payload = (Path(scaffold_dir) / "payload.bin").read_bytes()
    else:
        donor_payload = Path(scaffold.scaffold_payload_path()).read_bytes()

    BASE = ahcm.BASE
    offsets = [
        int.from_bytes(donor_payload[0x0C + i * 4 : 0x10 + i * 4], "little") - BASE
        for i in range(18)
    ]

    sections = {}
    for s in [0, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17]:
        start = offsets[s]
        end = offsets[s + 1] if s + 1 < 18 else len(donor_payload) - 6
        sections[s] = donor_payload[start:end]

    sec5_rel = offsets[5]
    dev_cnt = donor_payload[sec5_rel]
    device_cmd_offsets = []
    for d in range(dev_cnt):
        sub = int.from_bytes(donor_payload[sec5_rel + 1 + 3 * d : sec5_rel + 4 + 3 * d], "little") - BASE
        cnt = int.from_bytes(donor_payload[sub + 1 : sub + 3], "little")
        cmds = [
            int.from_bytes(donor_payload[sub + 3 + 3 * c : sub + 6 + 3 * c], "little") - BASE - 11
            for c in range(cnt)
        ]
        device_cmd_offsets.append(cmds)

    lay = layout.Layout()
    lay.out = bytearray(donor_payload[: offsets[0]])

    proj_activities = project.get("activities")
    if proj_activities:
        from . import screen_compiler

        sec9_body = bytearray(sections[9])
        mode_cnt = sec9_body[0]

        dev_map = {
            dev.get("label", f"Device {idx}"): idx
            for idx, dev in enumerate(project.get("devices", []))
        }
        dev_titles = {idx: lbl for lbl, idx in dev_map.items()}
        cmd_name_lookup: dict[tuple[int, str], int] = {}
        for idx, dev in enumerate(project.get("devices", [])):
            for c_idx, c_entry in enumerate(dev.get("commands", [])):
                if isinstance(c_entry, (list, tuple)) and c_entry:
                    cmd_name_lookup[(idx, str(c_entry[0]))] = c_idx
                elif isinstance(c_entry, str):
                    cmd_name_lookup[(idx, c_entry)] = c_idx
        dev_id_to_title = {
            str(dev.get("id", idx)): dev.get("label", f"Device {idx}")
            for idx, dev in enumerate(project.get("devices", []))
        }

        mode_ptrs = [
            int.from_bytes(sec9_body[1 + 3 * m : 4 + 3 * m], "little") - BASE
            for m in range(mode_cnt)
        ]

        act_by_id = {int(a["id"]): a for a in proj_activities if "id" in a}

        # 1. Standard Mode Physical Key Bindings (Section 09)
        for m in range(4, min(mode_cnt, 11)):
            if m not in act_by_id:
                continue

            act = act_by_id[m]
            std_mode = (act.get("modes") or {}).get("standard") or {}
            bindings = act.get("key_bindings")
            if bindings is None:
                bindings = std_mode.get("key_bindings", {})
            bindings = _merge_gui_hard_macros(
                bindings,
                act.get("hard_macros"),
                std_mode.get("hard_macros"),
                dev_id_to_title,
            )

            new_reg = act_mod.lower_activity_key_register(
                bindings,
                device_name_to_idx=dev_map,
                        command_name_lookup=cmd_name_lookup,
            )

            orig_ptr = mode_ptrs[m]
            orig_len = 1 + 4 * donor_payload[orig_ptr]

            if len(new_reg) == orig_len:
                lay.out[orig_ptr : orig_ptr + len(new_reg)] = new_reg
            else:
                alloc_offset = len(lay.out)
                lay.out.extend(new_reg)
                flash_ptr = BASE + alloc_offset
                sec9_body[1 + 3 * m : 4 + 3 * m] = flash_ptr.to_bytes(3, "little")

        sections[9] = bytes(sec9_body)

        # 2. Tripartite Screens (Standard, Sound, Picture) in Section 06
        sec6_body = bytearray(sections[6])
        sec6_changed = False

        objs = command_index.objects(donor_payload)

        def _get_cmds(obj_idx: int, depth: int = 3):
            if depth <= 0 or not (0 <= obj_idx < len(objs)):
                return []
            res = []
            for op_val, op_code in objs[obj_idx]["atoms"]:
                if op_code == 0x7D:
                    res.append((op_val >> 8, op_val & 0xFF))
                elif op_code == 0x7F:
                    res.extend(_get_cmds(op_val, depth - 1))
            return res

        for m in range(4, min(mode_cnt, 11)):
            if m not in act_by_id:
                continue

            act = act_by_id[m]
            modes = act.get("modes") or {}
            std_mode = modes.get("standard") or {}

            # 2a. Standard Mode Screen
            root_scr = act.get("root_screen")
            if root_scr is not None:
                donor_soft = act_mod.decompile_soft_buttons(donor_payload, root_scr)
                donor_title = ui_index.parse_screen(donor_payload, root_scr).get("title") or f"Activity {m}"
                curr_soft = act.get("soft_buttons")
                if curr_soft is None:
                    curr_soft = std_mode.get("soft_buttons")

                title = act.get("label") or donor_title
                if curr_soft is not None:
                    is_modified = (
                        title != donor_title
                        or _soft_labels(curr_soft) != donor_soft
                        or _soft_has_actions(curr_soft)
                    )
                    if is_modified:
                        new_tr_flash = screen_compiler.compile_activity_screen(
                            lay,
                            donor_payload,
                            screen_idx=root_scr,
                            title=title,
                            soft_buttons=curr_soft,
                            device_name_to_idx=dev_map,
                    command_name_lookup=cmd_name_lookup,
                        )
                        sec6_body[3 + 3 * root_scr : 6 + 3 * root_scr] = new_tr_flash.to_bytes(3, "little")
                        sec6_changed = True

            # 2b. Sound and Picture Mode Screens
            for mode_key, scr_field in (("sound", "sound_screen"), ("picture", "picture_screen")):
                mode_spec = modes.get(mode_key)
                if not mode_spec:
                    continue
                m_scr = mode_spec.get("screen", act.get(scr_field))
                if m_scr is None:
                    continue

                donor_mode = act_mod.decompile_mode_screen(
                    donor_payload, m_scr, dev_titles, _get_cmds
                )
                curr_m_soft = mode_spec.get("soft_buttons", donor_mode["soft_buttons"])
                curr_m_keys = _merge_gui_hard_macros(
                    mode_spec.get("key_bindings", donor_mode["key_bindings"]),
                    mode_spec.get("hard_macros"),
                    donor_mode.get("hard_macros"),
                    dev_id_to_title,
                )
                m_title = mode_spec.get("title") or donor_mode["title"] or f"{act.get('label', f'Activity {m}')} ({mode_key.capitalize()})"

                keys_changed = curr_m_keys != donor_mode["key_bindings"]
                soft_changed = (
                    m_title != donor_mode["title"]
                    or _soft_labels(curr_m_soft) != donor_mode["soft_buttons"]
                    or _soft_has_actions(curr_m_soft)
                )

                if not (keys_changed or soft_changed):
                    continue

                hk_bytes = None
                if keys_changed:
                    sc_orig = ui_index.parse_screen(donor_payload, m_scr)
                    preserved_nav = [
                        (hk["key_code"], hk["operand"], hk["category"])
                        for hk in sc_orig.get("header_keys", [])
                        if hk["category"] == 0x7E and hk["key_code"] in (0xA4, 0xA5)
                    ]
                    hk_bytes = act_mod.lower_activity_key_register(
                        curr_m_keys,
                        device_name_to_idx=dev_map,
                    command_name_lookup=cmd_name_lookup,
                        preserved_entries=preserved_nav,
                    )

                new_tr_flash = screen_compiler.compile_activity_screen(
                    lay,
                    donor_payload,
                    screen_idx=m_scr,
                    title=m_title,
                    soft_buttons=curr_m_soft,
                    device_name_to_idx=dev_map,
                    command_name_lookup=cmd_name_lookup,
                    header_kreg_bytes=hk_bytes,
                )
                sec6_body[3 + 3 * m_scr : 6 + 3 * m_scr] = new_tr_flash.to_bytes(3, "little")
                sec6_changed = True

        if sec6_changed:
            sections[6] = bytes(sec6_body)

    assemble.write_tables(
        lay,
        sections=sections,
        device_records=device_cmd_offsets,
    )
    image = assemble.seal_image(lay)
    validation = validate_payload(image, profile)

    out_name = settings.get("out_file") or "harmony555.bin"
    output = Path(out_name)
    if context and not output.is_absolute():
        output = Path(context.root) / output

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)

    return BuildResult(
        artifact=output,
        validation=validation,
        changes={},
        metadata={
            "backend": "harmony-555",
            "generator": "assemble",
            "size": len(image),
        },
    )
