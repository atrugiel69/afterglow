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


def build_project(project: dict, profile=None, context=None) -> BuildResult:
    """Outbound compiler: synthesize an AHCM image from an Afterglow project."""
    from . import ahcm, assemble, layout, scaffold, activities as act_mod

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

    # Dynamic extraction of sections
    sections = {}
    for s in [0, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17]:
        start = offsets[s]
        end = offsets[s + 1] if s + 1 < 18 else len(donor_payload) - 6
        sections[s] = donor_payload[start:end]

    # Command offsets from Section 05
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

    # --------------------------------------------------------------------------
    # Dynamic Activity Key Register Lowering & Section 09 Pointer Patching
    # --------------------------------------------------------------------------
    proj_activities = project.get("activities")
    if proj_activities:
        sec9_body = bytearray(sections[9])
        mode_cnt = sec9_body[0]

        dev_map = {
            dev.get("label", f"Device {idx}"): idx
            for idx, dev in enumerate(project.get("devices", []))
        }

        mode_ptrs = [
            int.from_bytes(sec9_body[1 + 3 * m : 4 + 3 * m], "little") - BASE
            for m in range(mode_cnt)
        ]

        act_by_id = {int(a["id"]): a for a in proj_activities if "id" in a}

        for m in range(4, min(mode_cnt, 11)):
            if m not in act_by_id:
                continue

            bindings = act_by_id[m].get("key_bindings", {})
            new_reg = act_mod.lower_activity_key_register(
                bindings,
                device_name_to_idx=dev_map,
            )

            orig_ptr = mode_ptrs[m]
            orig_len = 1 + 4 * donor_payload[orig_ptr]

            if len(new_reg) == orig_len:
                # In-place overwrite preserving bit parity
                lay.out[orig_ptr : orig_ptr + len(new_reg)] = new_reg
            else:
                # Relocate to heap tail before Section 00
                alloc_offset = len(lay.out)
                lay.out.extend(new_reg)
                flash_ptr = BASE + alloc_offset
                sec9_body[1 + 3 * m : 4 + 3 * m] = flash_ptr.to_bytes(3, "little")

        sections[9] = bytes(sec9_body)

        # ----------------------------------------------------------------------
        # Dynamic Soft Buttons & Screen 06 Lowering
        # ----------------------------------------------------------------------
        from . import screen_compiler
        sec6_body = bytearray(sections[6])
        sec6_changed = False

        for m in range(4, min(mode_cnt, 11)):
            if m not in act_by_id:
                continue

            act = act_by_id[m]
            root_scr = act.get("root_screen")
            if root_scr is None:
                continue

            donor_soft = act_mod.decompile_soft_buttons(donor_payload, root_scr)
            curr_soft = act.get("soft_buttons")

            if curr_soft is None:
                continue

            curr_labels = [
                e if (e is None or isinstance(e, str))
                else (e[0] if isinstance(e, (list, tuple)) else e.get("label"))
                for e in curr_soft
            ]

            is_modified = (curr_labels != donor_soft) or any(
                isinstance(e, dict) and any(k in e for k in ("operand", "object", "command", "category"))
                for e in curr_soft
            )

            if not is_modified:
                continue

            title = act.get("label") or f"Activity {m}"
            new_tr_flash = screen_compiler.compile_activity_screen(
                lay,
                donor_payload,
                screen_idx=root_scr,
                title=title,
                soft_buttons=curr_soft,
                device_name_to_idx=dev_map,
            )

            sec6_body[3 + 3 * root_scr : 6 + 3 * root_scr] = new_tr_flash.to_bytes(3, "little")
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
