import hashlib
from pathlib import Path
import pytest
from afterglow.backends.harmony_555 import backend

@pytest.fixture
def donor_bytes():
    payload_path = Path("src/afterglow/scaffolds/harmony-555/payload.bin")
    if not payload_path.is_file():
        pytest.skip("Scaffold donor payload not found")
    return payload_path.read_bytes()

def test_activity_key_roundtrip_bit_parity(donor_bytes):
    """Confirm round-trip build produces 100% identical byte image."""
    imp = backend.import_project(donor_bytes)
    res = backend.build_project(imp.project)
    built_bytes = Path(res.artifact).read_bytes()

    assert hashlib.sha256(built_bytes).hexdigest() == hashlib.sha256(donor_bytes).hexdigest()
    assert built_bytes == donor_bytes

def test_activity_key_inplace_mutation(donor_bytes):
    """Mutate an existing key binding in-place and verify container validity."""
    imp = backend.import_project(donor_bytes)
    proj = imp.project

    act4 = next(a for a in proj["activities"] if a["id"] == "4")
    act4["key_bindings"]["VolumeUp"] = {"type": "macro", "object": 0x01A4}

    res = backend.build_project(proj)
    mod_bytes = Path(res.artifact).read_bytes()

    reimp = backend.import_project(mod_bytes)
    reimp_act4 = next(a for a in reimp.project["activities"] if a["id"] == "4")

    assert reimp_act4["key_bindings"]["VolumeUp"]["object"] == 0x01A4
    assert len(mod_bytes) == len(donor_bytes)
    assert backend.validate_payload(mod_bytes).get("valid", True)

def test_activity_key_relocation_on_expansion(donor_bytes):
    """Add a new key binding, triggering heap tail relocation and Section 09 pointer patching."""
    imp = backend.import_project(donor_bytes)
    proj = imp.project

    act4 = next(a for a in proj["activities"] if a["id"] == "4")
    act4["key_bindings"]["Number1"] = {"type": "macro", "object": 0x0078}

    res = backend.build_project(proj)
    mod_bytes = Path(res.artifact).read_bytes()

    reimp = backend.import_project(mod_bytes)
    reimp_act4 = next(a for a in reimp.project["activities"] if a["id"] == "4")

    assert "Number1" in reimp_act4["key_bindings"]
    assert reimp_act4["key_bindings"]["Number1"]["object"] == 0x0078
    assert len(mod_bytes) > len(donor_bytes)
    assert backend.validate_payload(mod_bytes).get("valid", True)
