"""Assemble devices, activities, and variables for Harmony 555 (Architecture 9)."""

from __future__ import annotations

from . import activities, devices


def read_project(payload: bytes) -> dict:
    """Extract complete project AST: devices, signals, commands, activities, and soft buttons."""
    project_devs = devices.project_devices(payload)
    titles = devices.resolve_device_titles(payload)
    project_acts = activities.project_activities(payload, titles)

    return {
        "schema": "afterglow-project/1",
        "remote": "harmony-555",
        "devices": project_devs,
        "activities": project_acts,
        "settings": {"architecture": 9, "skin": 48},
    }
