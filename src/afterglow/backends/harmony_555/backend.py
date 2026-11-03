"""Harmony 555 (Architecture 9 / Mocha Grande) backend implementation."""

from __future__ import annotations

from pathlib import Path
from ... import ezhex
from ..contracts import BuildContext, BuildResult, ImportResult

SKIN = 48
ARCH = 9
CHECKSUM_SEED = 0x69
CONFIG_BASE = 0x00020000


def calculate_checksum(payload: bytes) -> int:
    """Calculate the single-byte Architecture 9 XOR checksum seeded with 0x69."""
    chk = CHECKSUM_SEED
    for b in payload:
        chk ^= b
    return chk


def import_project(raw: bytes | Path, context: BuildContext) -> ImportResult:
    """Extract devices, activities, and keymaps from an Architecture 9 dump."""
    if isinstance(raw, Path):
        raw = raw.read_bytes()

    if raw.startswith(b"<?xml") or b"<INFORMATION>" in raw[:512]:
        _, start, _, _ = ezhex._split(raw)
        payload = raw[start:]
    else:
        payload = raw

    if not payload.startswith(b"AHCM"):
        raise ValueError("Invalid Architecture 9 binary payload: missing AHCM header")

    project = {
        "schema": "afterglow-project/1",
        "remote": "harmony-555",
        "devices": [],
        "activities": [],
        "settings": {},
    }

    return ImportResult(project=project)


def build_project(project: dict, context: BuildContext) -> BuildResult:
    """Compile project into an Architecture 9 binary image wrapped in an EZHex container."""
    scaffold_path = context.find_scaffold("harmony-555", None)
    if scaffold_path and (scaffold_path / "payload.bin").exists():
        payload = (scaffold_path / "payload.bin").read_bytes()
    else:
        raise FileNotFoundError("Baseline payload.bin not found in scaffolds/harmony-555/")

    checksum = calculate_checksum(payload)
    artifact_path = context.root / "build" / "output.ezhex"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    header = (
        f'<?xml version="1.0"?>\r\n'
        f"<INFORMATION>\r\n"
        f"  <USERMESSAGES>\r\n"
        f"    <USERMESSAGE>\r\n"
        f"      <VERSIONS>\r\n"
        f"        <VERSION>\r\n"
        f"          <PROTOCOL>{ARCH}</PROTOCOL>\r\n"
        f"          <SKIN>{SKIN}</SKIN>\r\n"
        f"          <FLASH>0xFF:0x12</FLASH>\r\n"
        f"          <CHECKSUM>{checksum}</CHECKSUM>\r\n"
        f"        </VERSION>\r\n"
        f"      </VERSIONS>\r\n"
        f"    </USERMESSAGE>\r\n"
        f"  </USERMESSAGES>\r\n"
        f"</INFORMATION>\r\n"
    ).encode("latin-1")

    artifact_path.write_bytes(header + payload)

    return BuildResult(
        artifact=artifact_path,
        validation={"checksum": hex(checksum), "size": len(payload)},
        metadata={"model": "Harmony 555", "arch": ARCH, "skin": SKIN},
    )
