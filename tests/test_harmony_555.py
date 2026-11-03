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
