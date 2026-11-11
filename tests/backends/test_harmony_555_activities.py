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


def test_activity_soft_button_mutation_and_relocation(donor_bytes):
    """Mutate soft buttons for an activity, compile to heap tail, and verify round-trip import."""
    from afterglow.backends.harmony_555 import ui_index

    imp = backend.import_project(donor_bytes)
    proj = imp.project

    act4 = next(a for a in proj["activities"] if a["id"] == "4")
    custom_buttons = [
        {"label": "Power", "operand": 0x01A1},
        {"label": "Mute", "operand": 0x01A2},
        None,
        {"label": "Input", "operand": 0x01A3},
    ]
    act4["soft_buttons"] = custom_buttons

    res = backend.build_project(proj)
    mod_bytes = Path(res.artifact).read_bytes()

    assert len(mod_bytes) > len(donor_bytes)
    assert backend.validate_payload(mod_bytes).get("valid", True)

    # Re-import and check screen parsing
    sc = ui_index.parse_screen(mod_bytes, 70)
    assert len(sc["slots"]) == 1
    slot0 = sc["slots"][0]

    # Verify bezel key codes in slot register: 0xA7 (top_left), 0xA6 (top_right), 0x9E (bottom_right)
    kcodes = {k["key_code"]: k["operand"] for k in slot0["keys"]}
    assert kcodes[0xA7] == 0x01A1
    assert kcodes[0xA6] == 0x01A2
    assert kcodes[0x9E] == 0x01A3
    assert 0x9F not in kcodes  # None gap omitted

    # Verify bytecode text instructions
    texts = [i.get("text") for i in slot0["instructions"] if i.get("text")]
    assert "Power" in texts
    assert "Mute" in texts
    assert "Input" in texts


def test_activity_soft_button_multi_page_pagination(donor_bytes):
    """Compile multi-page soft buttons and verify slot count and pagination markers."""
    from afterglow.backends.harmony_555 import ui_index

    imp = backend.import_project(donor_bytes)
    proj = imp.project

    act4 = next(a for a in proj["activities"] if a["id"] == "4")
    # 6 buttons -> 2 pages
    act4["soft_buttons"] = [
        "Page1TL", "Page1TR", "Page1BL", "Page1BR",
        "Page2TL", "Page2TR"
    ]

    res = backend.build_project(proj)
    mod_bytes = Path(res.artifact).read_bytes()

    assert backend.validate_payload(mod_bytes).get("valid", True)

    sc = ui_index.parse_screen(mod_bytes, 70)
    assert len(sc["slots"]) == 2

    # Check slot 0 pagination
    p1_texts = [i.get("text") for i in sc["slots"][0]["instructions"] if i.get("text")]
    assert "Page1TL" in p1_texts
    assert "1" in p1_texts
    assert "OF" in p1_texts
    assert "2" in p1_texts

    # Check slot 1 pagination
    p2_texts = [i.get("text") for i in sc["slots"][1]["instructions"] if i.get("text")]
    assert "Page2TL" in p2_texts
    assert "2" in p2_texts
    assert "OF" in p2_texts
    assert "2" in p2_texts
