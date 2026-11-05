"""Binary record and table serialization primitives for Harmony 555 (Architecture 9)."""

from __future__ import annotations

import struct
from . import ahcm

ADDRESS_WINDOW = 0x10000  # 64 KiB native addressing window
CMAH_MARKER = b"CMAH"

# Object atoms
OBJECT_ATOM = 0x7F
COMMAND_ATOM = 0x7D
ACTIVITY_TRIGGER = 0x1F


def pointer24(offset: int) -> bytes:
    """Pack an offset into a 24-bit flash pointer (offset + BASE)."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("Native pointer must address a non-negative offset")
    absolute = ahcm.BASE + offset
    if absolute >= 1 << 24:
        raise ValueError("Native pointer does not fit in 24 bits")
    return absolute.to_bytes(3, "little")


def align_window(out: bytearray, size: int) -> int:
    """Pad layout so a sequential read does not span across a 64 KiB flash window boundary."""
    if size <= 0 or size > ADDRESS_WINDOW:
        raise ValueError("Native allocation must fit inside one 64 KiB window")
    if len(out) // ADDRESS_WINDOW != (len(out) + size - 1) // ADDRESS_WINDOW:
        out += bytes(ADDRESS_WINDOW - len(out) % ADDRESS_WINDOW)
    return len(out)


def counted_index(offsets: list[int], *, width: int, label: str) -> bytes:
    """A count of the given width (1 or 2 bytes), followed by 24-bit little-endian flash pointers."""
    limit = 0xFF if width == 1 else 0xFFFF
    if len(offsets) > limit:
        raise ValueError(f"{label} indexes at most {limit} entries")
    return (
        len(offsets).to_bytes(width, "little")
        + b"".join(pointer24(offset) for offset in offsets)
    )


# --- Section 06: Screen Engine & Slots ---

def screen_slot(key_register_offset: int, program_offset: int) -> bytes:
    """Build the 6-byte Section 06 screen slot record (kreg_ptr: u24, prog_ptr: u24)."""
    return pointer24(key_register_offset) + pointer24(program_offset)


def screen_trailer(flag: int, header_offset: int, slot_offsets: list[int]) -> bytes:
    """Build the Section 06 screen trailer linking one screen's slots."""
    if isinstance(flag, bool) or not (0 <= flag <= 0xFF):
        raise ValueError("Screen trailer flag must fit in 1 byte")
    if not slot_offsets or len(slot_offsets) > 0xFFFF:
        raise ValueError("A screen must declare between 1 and 65,535 slots")
    out = bytearray([flag]) + pointer24(header_offset)
    out += len(slot_offsets).to_bytes(2, "little")
    for offset in slot_offsets:
        out += pointer24(offset)
    return bytes(out)


def screen_index(trailer_offsets: list[int]) -> bytes:
    """Build the Section 06 master screen index."""
    if not trailer_offsets or len(trailer_offsets) > 0xFFFF:
        raise ValueError("Section 06 must index between 1 and 65,535 screens")
    out = bytearray(len(trailer_offsets).to_bytes(2, "little") + b"\x00")
    for offset in trailer_offsets:
        out += pointer24(offset)
    return bytes(out)


# --- Section 08 & Key Bindings ---

def key_register(entries: list[dict | tuple]) -> bytes:
    """Build an Architecture 9 key register binding hardware scan-codes to execution objects."""
    if not entries:
        return b"\x00\x00"

    out = bytearray([len(entries)])
    for entry in entries:
        if isinstance(entry, dict):
            key_code = entry["key_code"]
            operand = entry["operand"]
            cat = entry["category"]
        else:
            key_code, operand, cat = entry
        out += bytes([key_code]) + operand.to_bytes(2, "little") + bytes([cat])
    return bytes(out)


# --- Section 10: Execution Objects ---

def object_body(atoms: list[tuple[int, int]]) -> bytes:
    """Build an execution object body: 1-byte atom count followed by (operand: u16, opcode: u8) atoms."""
    if len(atoms) > 0xFF:
        raise ValueError("Object cannot exceed 255 atoms")
    out = bytearray([len(atoms)])
    for operand, opcode in atoms:
        out += operand.to_bytes(2, "little") + bytes([opcode])
    return bytes(out)


def object_index(object_offsets: list[int]) -> bytes:
    """Build the Section 10 master object index (2-byte count, then 24-bit pointers)."""
    return counted_index(object_offsets, width=2, label="Section 10 object table")


# --- Section 04 & 05: IR Commands & Waveforms ---


def command_record(
    record_offset: int,
    press_offset: int,
    hold_offset: int | None = None,
    release_offset: int | None = None,
    *,
    carrier_hz: int = 38000,
    prefix: bytes | None = None,
    timing_dict_ptr: int = 0x0485C2,
    trailing_flags: bytes = b"\x98\x00\x00\x01",
) -> bytes:
    """Build a 32-byte Architecture 9 command record (11-byte prefix + 21-byte payload)."""
    if prefix is not None:
        if len(prefix) != 11:
            raise ValueError(f"Architecture 9 command prefix must be 11 bytes, got {len(prefix)}")
        pref = prefix
    else:
        period_ns = round(1_000_000_000 / carrier_hz) if carrier_hz > 0 else 26315
        half_period_ns = period_ns // 2
        # Default transceiver descriptor: flags=03 02, type/sub=04 05 00
        pref = (
            b"\x03\x02\x04\x05\x00"
            + period_ns.to_bytes(3, "little")
            + half_period_ns.to_bytes(3, "little")
        )

    # Payload byte +11 starts with 0x05 (waveform encoding format)
    # Byte +12..+15: link pointer targeting record_offset + 4
    link_ptr = ahcm.BASE + record_offset + 4
    press_ptr = (ahcm.BASE + press_offset) if press_offset else 0
    hold_ptr = (ahcm.BASE + hold_offset) if hold_offset else 0
    rel_ptr = (ahcm.BASE + release_offset) if release_offset else 0

    body = (
        b"\x05"
        + link_ptr.to_bytes(3, "little")
        + b"\x01"  # Press phase present flag
        + press_ptr.to_bytes(3, "little")
        + hold_ptr.to_bytes(3, "little")
        + rel_ptr.to_bytes(3, "little")
        + timing_dict_ptr.to_bytes(3, "little")
        + trailing_flags
    )

    out = pref + body
    if len(out) != 32:
        raise ValueError(f"Command record must be exactly 32 bytes, got {len(out)}")
    return out


def command_subtable(record_offsets: list[int]) -> bytes:
    """Build a Section 05 per-device subtable pointing at command record byte +11."""
    if len(record_offsets) > 0xFFFF:
        raise ValueError("Device cannot exceed 65,535 command ordinals")
    out = bytearray(b"\x00" + len(record_offsets).to_bytes(2, "little"))
    for offset in record_offsets:
        target = ahcm.BASE + offset + 11
        out += target.to_bytes(3, "little")
    return bytes(out)


def device_command_index(subtable_offsets: list[int]) -> bytes:
    """Build Section 05 master device command index (1-byte device count, then 24-bit pointers)."""
    return counted_index(subtable_offsets, width=1, label="Section 05 device index")


def tokenized_waveform(table_offset: int, tokens: bytes) -> bytes:
    """Build a tokenized waveform: 3-byte timing table pointer, 2-byte token count, tokens."""
    return pointer24(table_offset) + len(tokens).to_bytes(2, "little") + tokens


# --- Section 00: SmartState Names ---

def name_chain(records: list[bytes] = ()) -> bytes:
    """Build Section 00 SmartState variable name chain (FEED .. BEEF)."""
    body = b"".join(records)
    if not body:
        return b"\xed\xfe\x05\x00\x00\xef\xbe"
    # Declared length is section length minus the 2-byte 0xEDFE header
    declared_len = len(body) + 3 + 2
    return b"\xed\xfe" + declared_len.to_bytes(3, "little") + body + b"\xef\xbe"


def name_record(record_type: int, identifier: int, name: str) -> bytes:
    """Serialize an Architecture 9 name record (0xA7 tag + u16 len + u16 type + u16 id + ascii)."""
    encoded = name.encode("ascii")
    length = 4 + len(encoded)  # 2 bytes record_type + 2 bytes identifier + raw ascii
    return bytes([0xA7]) + struct.pack("<HHH", length, record_type, identifier) + encoded
