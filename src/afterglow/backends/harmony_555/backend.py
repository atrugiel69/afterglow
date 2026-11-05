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
    from . import ahcm, assemble, layout, scaffold

    if profile is not None and hasattr(profile, "require_buildable"):
        profile.require_buildable()

    settings = project.get("settings") or {}
    scaffold_dir = context.find_scaffold(profile.id, context.root) if (context and profile) else None
    
    # Load base scaffold payload if present, or fall back to default
    if scaffold_dir and (Path(scaffold_dir) / "payload.bin").is_file():
        donor_payload = (Path(scaffold_dir) / "payload.bin").read_bytes()
    else:
        donor_payload = Path(scaffold.scaffold_payload_path()).read_bytes()

    # Re-assembly pipeline
    BASE = ahcm.BASE
    offsets = [int.from_bytes(donor_payload[0x0C + i * 4 : 0x10 + i * 4], "little") - BASE for i in range(18)]

    # Dynamic extraction of sections
    sections = {}
    for s in [0, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17]:
        start = offsets[s]
        end = offsets[s + 1] if s + 1 < 18 else len(donor_payload) - 6
        sections[s] = donor_payload[start:end]

    # Command offsets
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
    lay.out = bytearray(donor_payload[:offsets[0]])

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
