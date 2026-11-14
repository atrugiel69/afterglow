"""Assemble devices, activities, and variables for Harmony 555 (Architecture 9)."""

from __future__ import annotations

from . import activities, devices


def read_project(payload: bytes) -> dict:
    """Extract complete project AST: devices, signals, commands, activities, and soft buttons."""
    project_devs = devices.project_devices(payload)
    titles = devices.resolve_device_titles(payload)
    cmd_idx_to_name: dict[tuple[int, int], str] = {}
    for dev in project_devs:
        dev_idx = int(dev["id"])
        for c_idx, cmd_entry in enumerate(dev.get("commands", [])):
            if isinstance(cmd_entry, (list, tuple)) and cmd_entry:
                cmd_idx_to_name[(dev_idx, c_idx)] = str(cmd_entry[0])
            elif isinstance(cmd_entry, str):
                cmd_idx_to_name[(dev_idx, c_idx)] = cmd_entry

    project_acts = activities.project_activities(
        payload, titles, cmd_idx_to_name=cmd_idx_to_name
    )

    return {
        "schema": "afterglow-project/1",
        "remote": "harmony-555",
        "devices": project_devs,
        "activities": project_acts,
        "settings": {"architecture": 9, "skin": 48},
    }
