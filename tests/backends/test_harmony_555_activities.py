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

def test_activity_key_inplace_mutation(donor_bytes, tmp_path):
    """Mutate an existing key binding in-place and verify container validity."""
    imp = backend.import_project(donor_bytes)
    proj = imp.project

    act4 = next(a for a in proj["activities"] if a["id"] == "4")
    act4["key_bindings"]["VolumeUp"] = {"type": "macro", "object": 0x01A4}

    proj.setdefault("settings", {})["out_file"] = str(tmp_path / "harmony555.bin")
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

    # Verify bezel key codes in slot register: 0x9E (top_left), 0x9F (top_right), 0xA6 (bottom_right)
    kcodes = {k["key_code"]: k["operand"] for k in slot0["keys"]}
    assert kcodes[0x9E] == 0x01A1
    assert kcodes[0x9F] == 0x01A2
    assert kcodes[0xA6] == 0x01A3
    assert 0xA7 not in kcodes  # None gap omitted

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


def test_tripartite_mode_decompilation_and_key_nomenclature(donor_bytes):
    """Verify Sound (0xA5) and Picture (0xA4) modes decompile with distinct keys and screens."""
    from afterglow.backends.harmony_555 import keys

    assert keys.by_name("Picture") == 0xA4
    assert keys.by_name("Sound") == 0xA5
    assert keys.name_of(0xA4) == "Picture"
    assert keys.name_of(0xA5) == "Sound"
    assert keys.soft_key_name(0x9E) == "SoftTopLeft"
    assert keys.soft_key_name(0x9F) == "SoftTopRight"
    assert keys.soft_key_name(0xA7) == "SoftBottomLeft"
    assert keys.soft_key_name(0xA6) == "SoftBottomRight"

    imp = backend.import_project(donor_bytes)
    kodi = next(a for a in imp.project["activities"] if a["label"] == "Kodi")

    assert kodi["root_screen"] == 64
    assert kodi["sound_screen"] == 93
    assert kodi["picture_screen"] == 82

    assert set(kodi["modes"].keys()) == {"standard", "sound", "picture"}
    assert "DirectionUp" in kodi["modes"]["sound"]["key_bindings"]
    assert "Number1" in kodi["modes"]["picture"]["key_bindings"]
    assert "List" in kodi["modes"]["picture"]["soft_buttons"]
    assert "Home" in kodi["modes"]["picture"]["soft_buttons"]


def test_tripartite_mode_mutation_and_roundtrip(donor_bytes):
    """Mutate Sound and Picture mode key overrides and soft buttons, rebuild, and re-import."""
    from afterglow.backends.harmony_555 import ui_index

    imp = backend.import_project(donor_bytes)
    proj = imp.project
    kodi = next(a for a in proj["activities"] if a["label"] == "Kodi")

    # Mutate Sound mode physical key override + soft button
    kodi["modes"]["sound"]["key_bindings"]["DirectionUp"] = {"type": "macro", "object": 0x01B0}
    kodi["modes"]["sound"]["soft_buttons"] = [
        {"label": "Bass +", "operand": 0x01B1},
        {"label": "Bass -", "operand": 0x01B2},
        None,
        None,
    ]

    # Mutate Picture mode soft button
    kodi["modes"]["picture"]["soft_buttons"] = [
        {"label": "Cinema", "operand": 0x01B3},
        {"label": "Game", "operand": 0x01B4},
        None,
        None,
    ]

    res = backend.build_project(proj)
    mod_bytes = Path(res.artifact).read_bytes()
    assert backend.validate_payload(mod_bytes).get("valid", True)

    # Verify modal 0x7E navigation links (Sound 0xA5 and Picture 0xA4) remain intact on Sound screen
    sc_sound = ui_index.parse_screen(mod_bytes, 93)
    nav_links = {
        hk["key_code"]: hk["operand"]
        for hk in sc_sound["header_keys"]
        if hk["category"] == 0x7E
    }
    assert nav_links[0xA5] == 64  # Sound toggles back to Standard root screen 64
    assert nav_links[0xA4] == 82  # Picture switches to Picture screen 82

    # Re-import project and confirm round-trip fidelity
    reimp = backend.import_project(mod_bytes)
    kodi_re = next(a for a in reimp.project["activities"] if a["label"] == "Kodi")
    assert kodi_re["modes"]["sound"]["key_bindings"]["DirectionUp"]["object"] == 0x01B0
    assert kodi_re["modes"]["sound"]["soft_buttons"][0] == "Bass +"
    assert kodi_re["modes"]["sound"]["soft_buttons"][1] == "Bass -"
    assert kodi_re["modes"]["picture"]["soft_buttons"][0] == "Cinema"
    assert kodi_re["modes"]["picture"]["soft_buttons"][1] == "Game"


def test_multiline_soft_button_wrapping_and_roundtrip(donor_bytes):
    """Verify 2-line soft-button labels wrap across vertical bands and decompile cleanly."""
    from afterglow.backends.harmony_555 import ui_index

    imp = backend.import_project(donor_bytes)
    proj = imp.project
    act4 = next(a for a in proj["activities"] if a["id"] == "4")

    act4["soft_buttons"] = [
        {"label": "Climate Control", "operand": 0x01C0},
        {"label": "Film\nMaker", "operand": 0x01C1},
        {"label": "Sub Trim", "operand": 0x01C2},
        {"label": "Aspect Ratio", "operand": 0x01C3},
    ]

    res = backend.build_project(proj)
    mod_bytes = Path(res.artifact).read_bytes()
    assert backend.validate_payload(mod_bytes).get("valid", True)

    sc = ui_index.parse_screen(mod_bytes, 70)
    ys = {i.get("y") for i in sc["slots"][0]["instructions"] if i.get("text")}
    assert {13, 24, 35, 46}.issubset(ys)

    reimp = backend.import_project(mod_bytes)
    act4_re = next(a for a in reimp.project["activities"] if a["id"] == "4")
    assert act4_re["soft_buttons"] == ["Climate Control", "Film Maker", "Sub Trim", "Aspect Ratio"]


def test_gui_tripartite_editor_roundtrip(donor_bytes, qapp_or_skip, tmp_path):
    """Open an imported Harmony 555 activity in ActivityEditor, switch modes, edit, and rebuild."""
    from afterglow import remotes
    from afterglow.gui.activity_buttons import ScreenButtonsPage
    from afterglow.gui.activity_wizard import ActivityEditor, ActivityHardButtonsPage

    profile = remotes.get("harmony-555")
    assert profile.can("tripartite_modes")

    imp = backend.import_project(donor_bytes)
    proj = imp.project
    kodi = next(a for a in proj["activities"] if a["label"] == "Kodi")

    editor = ActivityEditor(proj["devices"], existing=kodi, remote=profile)

    sb_page = editor.findChild(ScreenButtonsPage)
    hb_page = editor.findChild(ActivityHardButtonsPage)
    assert sb_page is not None and hb_page is not None

    assert set(sb_page._mode_btns.keys()) == {"standard", "sound", "picture"}
    assert set(hb_page._mode_btns.keys()) == {"standard", "sound", "picture"}

    # Switch ScreenButtonsPage to Sound mode and edit a soft button
    sb_page._switch_mode("sound")
    assert sb_page._active_mode == "sound"
    sb_entries = sb_page._entries()
    sb_entries[0] = {"label": "Night Mode", "device": "0", "command": "PowerToggle"}
    sb_page._set_rows(sb_entries)

    # Switch ActivityHardButtonsPage to Picture mode and assign a physical button override
    hb_page._switch_mode("picture")
    assert hb_page._active_mode == "picture"
    dev0 = proj["devices"][0]
    cmd0 = dev0["commands"][0][0]
    hb_page.macros["Number9"] = [["command", str(dev0["id"]), cmd0]]

    # Collect edited activity spec and rebuild AHCM binary
    edited_kodi = editor._collect()
    assert edited_kodi["modes"]["sound"]["soft_buttons"][0]["label"] == "Night Mode"
    assert edited_kodi["modes"]["picture"]["hard_macros"]["Number9"] == [["command", str(dev0["id"]), cmd0]]

    for i, act in enumerate(proj["activities"]):
        if act["id"] == edited_kodi["id"]:
            proj["activities"][i] = edited_kodi
            break

    res = backend.build_project(proj)
    mod_bytes = Path(res.artifact).read_bytes()
    assert backend.validate_payload(mod_bytes).get("valid", True)

    reimp = backend.import_project(mod_bytes)
    kodi_re = next(a for a in reimp.project["activities"] if a["label"] == "Kodi")
    assert kodi_re["modes"]["sound"]["soft_buttons"][0] == "Night Mode"
    assert kodi_re["modes"]["picture"]["key_bindings"]["Number9"]["command"] == cmd0


def test_activity_startup_power_and_enter_roundtrip(donor_bytes, tmp_path):
    """Mutate power lists and enter steps; verify Section 10 round-trip."""
    from afterglow.backends.harmony_555 import command_index

    imp = backend.import_project(donor_bytes)
    proj = imp.project
    kodi = next(a for a in proj["activities"] if a["label"] == "Kodi")

    assert isinstance(kodi["power_on_devices"], list)
    assert isinstance(kodi["power_off_devices"], list)
    assert isinstance(kodi["enter"], list)
    assert isinstance(kodi["leave"], list)

    orig_objs = command_index.objects(donor_bytes)
    assert len(orig_objs) == 1287

    cmd_dev1 = proj["devices"][1]["commands"][2][0]
    reversed_on = list(reversed(kodi["power_on_devices"]))
    kodi["power_on_devices"] = reversed_on
    kodi["enter"] = list(kodi["enter"]) + [
        ["command", "1", cmd_dev1],
        ["delay", "1", 750],
    ]

    proj.setdefault("settings", {})["out_file"] = str(tmp_path / "harmony555.bin")
    res = backend.build_project(proj)
    mod_bytes = Path(res.artifact).read_bytes()
    assert backend.validate_payload(mod_bytes).get("ok") is True

    # Donor object indices 0..1286 remain unshifted; new objects appended at 1287+
    mod_objs = command_index.objects(mod_bytes)
    assert len(mod_objs) == 1290
    assert mod_objs[983]["atoms"] == orig_objs[983]["atoms"]

    reimp = backend.import_project(mod_bytes)
    kodi_re = next(a for a in reimp.project["activities"] if a["label"] == "Kodi")
    assert kodi_re["power_on_devices"] == reversed_on
    assert kodi_re["enter"][-2:] == [["command", "1", cmd_dev1], ["delay", "1", 750]]


def test_activity_leave_sequence_roundtrip(donor_bytes, tmp_path):
    """Add shutdown commands and delays to leave list and verify round-trip."""
    imp = backend.import_project(donor_bytes)
    proj = imp.project
    kodi = next(a for a in proj["activities"] if a["label"] == "Kodi")
    cmd_dev0 = proj["devices"][0]["commands"][1][0]
    cmd_dev2 = proj["devices"][2]["commands"][3][0]

    kodi["leave"] = [
        ["command", "0", cmd_dev0],
        ["delay", "0", 500],
        ["command", "2", cmd_dev2],
    ]

    proj.setdefault("settings", {})["out_file"] = str(tmp_path / "harmony555.bin")
    res = backend.build_project(proj)
    mod_bytes = Path(res.artifact).read_bytes()
    assert backend.validate_payload(mod_bytes).get("ok") is True

    reimp = backend.import_project(mod_bytes)
    kodi_re = next(a for a in reimp.project["activities"] if a["label"] == "Kodi")
    assert len(kodi_re["leave"]) == 3
    assert kodi_re["leave"][0][0:2] == ["command", "0"]
    assert kodi_re["leave"][0][2] == reimp.project["devices"][0]["commands"][1][0]
    assert kodi_re["leave"][1] == ["delay", "0", 500]
    assert kodi_re["leave"][2] == ["command", "2", cmd_dev2]
