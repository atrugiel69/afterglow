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
    """Outbound compiler: generate an AHCM image from an Afterglow project."""
    raise NotImplementedError("Outbound compiler (generate.py / assemble.py) is queued for Phase 3.")
