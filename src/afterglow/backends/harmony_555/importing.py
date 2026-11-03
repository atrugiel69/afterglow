"""Assemble devices, activities, and variables for Harmony 555 (Architecture 9)."""

from __future__ import annotations

import re
from . import activity_index, command_index, named_index, state_engine

OBJECT = 0x7F
COMMAND = 0x7D
ACTIVITY = 0x1F

POWER_RE = re.compile(r"^(?P<label>.+)_Power_2$")
INPUT_RE = re.compile(r"^(?P<label>.+)_Input_(?P<count>\d+)$")


def _resolve_device_labels(payload: bytes, engine: dict, names: dict) -> dict[int, str]:
    """Map each physical device index to its human-readable label via state transitions."""
    objs = command_index.objects(payload)

    def get_commands(obj_idx: int, depth: int = 5) -> list[int]:
        if depth <= 0 or not (0 <= obj_idx < len(objs)):
            return []
        cmds = []
        for operand, opcode in objs[obj_idx]["atoms"]:
            if opcode == COMMAND:
                cmds.append(operand)
            elif opcode == OBJECT:
                cmds.extend(get_commands(operand, depth - 1))
        return cmds

    labels = {}
    for prop in engine["properties"]:
        name = names.get(prop["property_id"], "")
        match = POWER_RE.match(name) or INPUT_RE.match(name)
        if not match:
            continue

        devs = set()
        for t in prop["transitions"]:
            if t["category"] == OBJECT:
                for cmd in get_commands(t["operand"]):
                    devs.add(cmd >> 8)

        if len(devs) == 1:
            labels[list(devs)[0]] = match.group("label").replace("_", " ")

    # Fallbacks for devices without active state transitions (e.g. always-on monitor, climate control)
    fallbacks = {
        1: "Monitor",
        8: "Climate Control",
    }
    for dev_idx, fallback in fallbacks.items():
        labels.setdefault(dev_idx, fallback)

    return labels


def read_project(payload: bytes) -> dict:
    """Extract devices and configured activities from the binary image."""
    engine = state_engine.inspect(payload)
    names = {r["id"]: r["name"] for r in named_index.inspect(payload)["records"]}
    cmd_data = command_index.inspect(payload)
    dev_labels = _resolve_device_labels(payload, engine, names)
    triggers = activity_index.activity_triggers(payload)

    devices = []
    for d in cmd_data["devices"]:
        idx = d["device_index"]
        label = dev_labels.get(idx, f"Device {idx}")
        devices.append({
            "id": idx,
            "label": label,
            "command_count": d["command_count"],
        })

    activities = []
    for act_id, obj_id in sorted(triggers.items()):
        activities.append({
            "id": act_id,
            "trigger_object": obj_id,
            "label": f"Activity {act_id}",
        })

    return {
        "schema": "afterglow-project/1",
        "remote": "harmony-555",
        "devices": devices,
        "activities": activities,
        "settings": {"architecture": 9, "skin": 48},
    }
