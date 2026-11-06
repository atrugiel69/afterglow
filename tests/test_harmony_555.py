"""Regression test suite for Logitech Harmony 555 (Architecture 9 / Mocha Grande)."""

from __future__ import annotations

from pathlib import Path
import pytest

from afterglow.backends.harmony_555 import ahcm, backend, command_index, devices, drawing, ui_index

SCAFFOLD = Path("src/afterglow/scaffolds/harmony-555/payload.bin")


@pytest.fixture(scope="module")
def payload():
    if not SCAFFOLD.is_file():
        pytest.skip(f"Scaffold binary not found at {SCAFFOLD}")
    return SCAFFOLD.read_bytes()


def test_ahcm_container_validation(payload):
    validation = ahcm.validate(payload)
    assert validation["ok"] is True
    assert validation["size"] == len(payload)


def test_drawing_bytecode_roundtrip(payload):
    # Verify drawing bytecode disassembles and reassembles with zero data loss
    total_screens = ui_index.screen_count(payload)
    assert total_screens == 281

    tested = 0
    for s in range(min(50, total_screens)):
        sc = ui_index.parse_screen(payload, s)
        for sl in sc["slots"]:
            prog_off = sl["prog_offset"]
            insts, length = drawing.disassemble(payload, prog_off)
            orig = payload[prog_off : prog_off + length]
            rebuilt = drawing.assemble(insts)
            assert orig == rebuilt
            tested += 1
    assert tested > 0


def test_command_index_waveform_decompression(payload):
    data = command_index.inspect(payload)
    assert data["device_count"] == 10
    assert data["total_commands"] == 611

    # Verify carrier frequencies across distinctive devices
    devices_by_idx = {d["device_index"]: d for d in data["devices"]}
    
    # Standard 38kHz device
    assert devices_by_idx[0]["commands"][0]["signal"]["carrier_hz"] == 38001
    # 36.2kHz Microsoft MCE / HTPC device
    assert devices_by_idx[2]["commands"][0]["signal"]["carrier_hz"] == 36200
    # Climate Control state dump pulse stream
    climate_pulses = devices_by_idx[8]["commands"][0]["signal"]["press"]
    assert len(climate_pulses) == 585


def test_project_import_pipeline(payload):
    result = backend.import_project(payload)
    project = result.project

    assert project["schema"] == "afterglow-project/1"
    assert project["remote"] == "harmony-555"
    assert len(project["devices"]) == 10
    assert len(project["activities"]) == 7

    # Verify physical appliances resolved without fallback markers
    device_names = [d["label"] for d in project["devices"]]
    assert "Denon X3800H" in device_names
    assert "Monitor" in device_names
    assert "-HTPC-" in device_names
    assert "Yamaha RX-A3010" in device_names
    assert "AM6b" in device_names
    assert "TV" in device_names
    assert "TiviMate" in device_names
    assert "DuneHD" in device_names
    assert "Climate Control" in device_names
    assert "Squeezebox" in device_names

    # Verify activity soft-button lists
    act_by_name = {a["label"]: a for a in project["activities"]}
    assert "HTPC" in act_by_name
    assert "DynVol" in act_by_name["HTPC"]["soft_buttons"]
    assert "Steam" in act_by_name["HTPC"]["soft_buttons"]
    assert "PwrOff" in act_by_name["HTPC"]["soft_buttons"]

    assert "Watch TV" in act_by_name
    assert "Amazon" in act_by_name["Watch TV"]["soft_buttons"]
    assert "HDMI 4" in act_by_name["Watch TV"]["soft_buttons"]


def test_ahcm_container_reassembly_parity(payload):
    """Verify 100% byte-for-byte round-trip assembly across all 18 sections."""
    import struct
    from afterglow.backends.harmony_555 import ahcm, assemble, layout, named_index, scaffold, structures

    BASE = ahcm.BASE

    # 1. Parse Section 00 SmartState names
    name_data = named_index.inspect(payload)
    rebuilt_sec0 = structures.name_chain([
        structures.name_record(r["type"], r["id"], r["name"])
        for r in name_data["records"]
    ])

    # 2. Extract Section 03
    offsets = [struct.unpack_from("<I", payload, 0x0C + i * 4)[0] - BASE for i in range(18)]
    sec3_bytes = payload[offsets[3] : offsets[4]]

    # 3. Extract device command runs for Sections 04 & 05
    sec5_rel = offsets[5]
    dev_cnt = payload[sec5_rel]
    device_cmd_offsets = []
    for d in range(dev_cnt):
        sub = int.from_bytes(payload[sec5_rel + 1 + 3 * d : sec5_rel + 4 + 3 * d], "little") - BASE
        cnt = struct.unpack_from("<H", payload, sub + 1)[0]
        cmds = [
            int.from_bytes(payload[sub + 3 + 3 * c : sub + 6 + 3 * c], "little") - BASE - 11
            for c in range(cnt)
        ]
        device_cmd_offsets.append(cmds)

    # 4. Extract remaining sections
    sections = {
        0: rebuilt_sec0,
        3: sec3_bytes,
    }
    for s in [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17]:
        start = offsets[s]
        end = offsets[s + 1] if s + 1 < 18 else len(payload) - 6
        sections[s] = payload[start:end]

    # 5. Build and seal
    lay = layout.Layout()
    lay.out = bytearray(payload[:offsets[0]])

    assemble.write_tables(
        lay,
        sections=sections,
        device_records=device_cmd_offsets,
    )
    sealed = assemble.seal_image(lay)

    val = ahcm.validate(sealed)
    assert val["ok"] is True
    assert len(sealed) == len(payload)
    assert sealed == payload


def test_build_project_roundtrip(payload, tmp_path):
    """Verify that importing and rebuilding an image yields an identical binary artifact."""
    from afterglow.backends import contracts
    from afterglow.backends.harmony_555 import backend

    # 1. Import project
    import_res = backend.import_project(payload)
    project = import_res.project

    # 2. Build project
    context = contracts.BuildContext(
        root=tmp_path,
        log=lambda msg: None,
        find_scaffold=lambda remote_id, root: Path("src/afterglow/scaffolds/harmony-555"),
    )
    build_res = backend.build_project(project, context=context)

    # 3. Assert output
    assert build_res.artifact.is_file()
    assert build_res.validation["ok"] is True
    assert build_res.artifact.read_bytes() == payload


def test_keys_scan_mapping():
    """Verify Harmony 555 physical key matrix mappings and helper integrity."""
    from afterglow.backends.harmony_555 import keys

    # 1. Total declared keys (44 physical keys including dedicated Teletext colors)
    assert len(keys.SCANS) == 44
    assert len(keys.COMMAND_KEYS) == 41
    assert set(keys.FLOW) == {keys.ACTIVITIES, keys.OFF, keys.HELP}

    # 2. Key anchors verified via RC6 / NEC signals
    anchors = {
        "Number1": 43,
        "Number2": 2,
        "Number3": 1,
        "Number4": 3,
        "Number5": 10,
        "Number6": 9,
        "Number7": 11,
        "Number8": 18,
        "Number9": 17,
        "Number0": 26,
        "Play": 4,
        "Pause": 12,
        "Stop": 7,
        "Rewind": 13,
        "FastForward": 14,
        "DirectionUp": 45,
        "DirectionDown": 49,
        "DirectionLeft": 53,
        "DirectionRight": 44,
        "Select": 52,
        "VolumeUp": 51,
        "VolumeDown": 50,
        "VolumeMute": 61,
        "ChannelUp": 57,
        "ChannelDown": 41,
        "Red": 27,
        "Green": 35,
        "Yellow": 34,
        "Blue": 33,
    }
    for name, expected_scan in anchors.items():
        assert keys.SCANS[name] == expected_scan
        assert keys.by_name(name) == 0x80 | expected_scan
        assert keys.name_of(0x80 | expected_scan) == name

    # 3. Bidirectional round-trip for all command keys
    for name in keys.COMMAND_KEYS:
        code_val = keys.by_name(name)
        assert code_val is not None
        assert keys.name_of(code_val) == name

    # 4. Command ownership logic
    assert keys.is_own_command("VolumeUp", "VolumeUp") is True
    assert keys.is_own_command("Number1", "1") is True
    assert keys.is_own_command("VolumeMute", "Mute") is True
    assert keys.is_own_command("PrevChannel", "Prev") is True
    assert keys.is_own_command("Play", "VolumeUp") is False
