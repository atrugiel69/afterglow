"""Authoritative device-to-command index for Harmony 555 (Architecture 9)."""

from __future__ import annotations

import struct
from . import ahcm

OBJECT = 0x7F
COMMAND = 0x7D
ACTIVITY = 0x1F


def _u24(payload: bytes, offset: int) -> int:
    return int.from_bytes(payload[offset : offset + 3], "little")


def _read_timing_table(payload: bytes, table_offset: int) -> list[list[int]]:
    """Parse a Section 04 timing dictionary table into lists of signed microsecond pulses."""
    if not (0 <= table_offset < len(payload)):
        return []

    entry_count = payload[table_offset]
    entries = []

    for i in range(entry_count):
        ptr_pos = table_offset + 1 + 3 * i
        if ptr_pos + 3 > len(payload):
            break
        entry_ptr = _u24(payload, ptr_pos) - ahcm.BASE
        if not (0 <= entry_ptr + 2 <= len(payload)):
            entries.append([])
            continue

        word_count = struct.unpack_from("<H", payload, entry_ptr)[0]
        pulses = []
        for w in range(word_count):
            w_pos = entry_ptr + 2 + 2 * w
            if w_pos + 2 > len(payload):
                break
            word = struct.unpack_from("<H", payload, w_pos)[0]
            dur = word & 0x7FFF
            if dur > 0:
                pulses.append(dur if (word & 0x8000) else -dur)
        entries.append(pulses)

    return entries


def _decode_tokenized_waveform(payload: bytes, offset: int) -> list[int]:
    """Decode an Architecture 9 tokenized waveform into a flat pulse list."""
    if not (0 <= offset + 5 <= len(payload)):
        return []

    table_ptr = _u24(payload, offset) - ahcm.BASE
    token_count = struct.unpack_from("<H", payload, offset + 3)[0]

    timing_table = _read_timing_table(payload, table_ptr)
    if not timing_table:
        return []

    pulses: list[int] = []
    tokens = payload[offset + 5 : offset + 5 + token_count]

    for token in tokens:
        if token < len(timing_table):
            for pulse in timing_table[token]:
                if pulses and (pulses[-1] > 0) == (pulse > 0):
                    pulses[-1] += pulse
                else:
                    pulses.append(pulse)

    return pulses


def decode_command_record(payload: bytes, offset: int) -> dict:
    """Decode a Section 05 command record into carrier frequency and pulse timing."""
    if offset < 0 or offset + 20 > len(payload):
        raise ValueError(f"Command record offset {offset:#x} out of range")

    period_ns = _u24(payload, offset + 5)
    carrier_hz = round(1_000_000_000 / period_ns) if period_ns > 0 else 38000

    press_ptr = _u24(payload, offset + 16) - ahcm.BASE
    hold_ptr = _u24(payload, offset + 19) - ahcm.BASE

    press_pulses = (
        _decode_tokenized_waveform(payload, press_ptr)
        if 0 <= press_ptr < len(payload)
        else []
    )
    hold_pulses = (
        _decode_tokenized_waveform(payload, hold_ptr)
        if 0 <= hold_ptr < len(payload)
        else []
    )

    return {
        "carrier_hz": carrier_hz,
        "period_ns": period_ns,
        "press": press_pulses,
        "hold": hold_pulses,
    }


def objects(payload: bytes) -> list[dict]:
    """Parse the Section 10 master object table."""
    start = struct.unpack_from("<I", payload, 0x0C + 10 * 4)[0] - ahcm.BASE
    count = struct.unpack_from("<H", payload, start)[0]
    out = []
    for i in range(count):
        pos = start + 2 + 3 * i
        ptr = _u24(payload, pos) - ahcm.BASE
        atom_count = payload[ptr]
        atoms = tuple(
            struct.unpack_from("<HB", payload, ptr + 1 + 3 * a)
            for a in range(atom_count)
        )
        out.append({"index": i, "offset": ptr, "atoms": atoms})
    return out


def inspect(payload: bytes) -> dict:
    """Read Section 05 and map all devices and command IDs with their waveforms."""
    start = struct.unpack_from("<I", payload, 0x0C + 5 * 4)[0] - ahcm.BASE
    device_count = payload[start]

    devices = []
    for dev_idx in range(device_count):
        sub_ptr = _u24(payload, start + 1 + 3 * dev_idx) - ahcm.BASE
        cmd_count = struct.unpack_from("<H", payload, sub_ptr + 1)[0]

        commands = []
        for ordinal in range(cmd_count):
            cmd_id = (dev_idx << 8) | ordinal
            target = _u24(payload, sub_ptr + 3 + 3 * ordinal) - ahcm.BASE
            rec_off = target - 11
            signal = decode_command_record(payload, rec_off)
            commands.append({
                "command_id": cmd_id,
                "record_offset": rec_off,
                "signal": signal,
            })

        devices.append({
            "device_index": dev_idx,
            "subtable_offset": sub_ptr,
            "command_count": cmd_count,
            "commands": commands,
        })

    return {
        "device_count": device_count,
        "total_commands": sum(d["command_count"] for d in devices),
        "devices": devices,
    }
