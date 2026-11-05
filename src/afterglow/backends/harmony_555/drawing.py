"""Section 06 Drawing Bytecode Virtual Machine (Disassembler & Assembler) for Architecture 9."""

from __future__ import annotations

import struct
from . import ahcm

BASE = ahcm.BASE

# Opcode constants
END = 0x00
LINE = 0x03
TEXT = 0x04
INLINE_TEXT = 0x05
ATTR = 0x10
BOX = 0x16
RETURN = 0x17

# Architecture 9 Verified 70-Glyph Master Repertoire Table
GLYPHS: dict[int, str] = {
    0x01: "H", 0x02: "y", 0x03: "O", 0x04: "F", 0x05: "a", 0x06: "T",
    0x07: "e", 0x08: "r", 0x09: "m", 0x0A: "i", 0x0B: "n", 0x0C: "t",
    0x0D: " ", 0x0E: "E", 0x0F: "U", 0x10: "S", 0x11: "B", 0x12: "C",
    0x13: "N", 0x14: "D", 0x15: "p", 0x16: "g", 0x17: "d", 0x18: "u",
    0x19: "c", 0x1A: "s", 0x1B: "0", 0x1C: ":", 0x1D: "o", 0x1E: "2",
    0x1F: "f", 0x20: "l", 0x21: "L", 0x22: ".", 0x23: "w", 0x24: "G",
    0x25: "W", 0x26: "b", 0x27: "I", 0x28: "z", 0x29: "k", 0x2A: "R",
    0x2B: "P", 0x2C: "h", 0x2D: "A", 0x2E: "M", 0x2F: "q", 0x30: "\\",
    0x31: "v", 0x32: "V", 0x33: "X", 0x34: "3", 0x35: "8", 0x36: "6",
    0x37: "Y", 0x38: "-", 0x39: "1", 0x3A: "x", 0x3B: "4", 0x3C: "9",
    0x3D: "5", 0x3E: "K", 0x3F: "/", 0x40: "7", 0x41: "?", 0x42: "(",
    0x43: ")", 0x44: "Z", 0x45: "Q", 0x46: "+",
}

CHARS: dict[str, int] = {v: k for k, v in GLYPHS.items() if v}

# Address fields remapped when code blocks move across layouts
ADDRESS_FIELDS = {
    "text": ("target_ptr", "target"),
}


def decode_glyphs(raw: bytes) -> str:
    """Translate raw glyph codes to Unicode string."""
    return "".join(GLYPHS.get(b, "") for b in raw).strip()


def encode_glyphs(text: str) -> bytes:
    """Translate Unicode string to Architecture 9 glyph codes."""
    return bytes(CHARS.get(c, 0x0D) for c in text)


def disassemble(payload: bytes, offset: int) -> tuple[list[dict], int]:
    """Disassemble an Architecture 9 drawing bytecode program into an AST."""
    cur = offset
    end = min(offset + 400, len(payload))
    instructions = []

    while cur < end:
        op = payload[cur]
        inst_offset = BASE + cur

        if op == END:
            instructions.append({"op": "end", "offset": inst_offset})
            cur += 1
            break
        elif op == BOX:
            line = payload[cur + 1]
            box_type = payload[cur + 2]
            x, y = struct.unpack_from("<HH", payload, cur + 3)
            w, h = payload[cur + 7], payload[cur + 8]
            style = int.from_bytes(payload[cur + 9 : cur + 12], "little")
            instructions.append({
                "op": "box",
                "offset": inst_offset,
                "line": line,
                "type": box_type,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "style": style,
            })
            cur += 12
        elif op == LINE:
            line_type = payload[cur + 1]
            geom = payload[cur + 2 : cur + 7]
            style = int.from_bytes(payload[cur + 7 : cur + 10], "little")
            instructions.append({
                "op": "line",
                "offset": inst_offset,
                "line_type": line_type,
                "geom": geom,
                "style": style,
            })
            cur += 10
        elif op in (ATTR, 0x11):
            instructions.append({
                "op": "font",
                "offset": inst_offset,
                "opcode": op,
                "font_id": payload[cur + 1],
            })
            cur += 2
        elif op == RETURN:
            instructions.append({"op": "return", "offset": inst_offset})
            cur += 1
        elif op == TEXT:
            x, y = payload[cur + 1], payload[cur + 2]
            target_ptr = int.from_bytes(payload[cur + 3 : cur + 6], "little")
            target_rel = target_ptr - BASE
            text_str = ""
            if 0 <= target_rel < len(payload):
                null_pos = payload.find(b"\x00", target_rel)
                raw = payload[target_rel : null_pos if null_pos != -1 else target_rel + 20]
                text_str = decode_glyphs(raw)
            instructions.append({
                "op": "text",
                "offset": inst_offset,
                "x": x,
                "y": y,
                "target_ptr": target_ptr,
                "text": text_str,
            })
            cur += 6
        elif op == INLINE_TEXT:
            x, y = payload[cur + 1], payload[cur + 2]
            null_pos = payload.find(b"\x00", cur + 3)
            if null_pos != -1 and null_pos - (cur + 3) < 40:
                raw = payload[cur + 3 : null_pos]
                text_str = decode_glyphs(raw)
                instructions.append({
                    "op": "inline_text",
                    "offset": inst_offset,
                    "x": x,
                    "y": y,
                    "glyphs": raw,
                    "text": text_str,
                })
                cur = null_pos + 1
            else:
                cur += 4
        else:
            raise ValueError(f"Unknown opcode {hex(op)} at {hex(cur)}")

    return instructions, cur - offset


def encode_instruction(inst: dict) -> bytes:
    """Encode a single drawing instruction to bytecode."""
    op = inst.get("op") or inst.get("opcode")
    if op == "end":
        return bytes([END])
    elif op == "box":
        return bytes([BOX, inst["line"], inst["type"]]) + \
               struct.pack("<HH", inst["x"], inst["y"]) + \
               bytes([inst["w"], inst["h"]]) + \
               inst["style"].to_bytes(3, "little")
    elif op == "line":
        return bytes([LINE, inst["line_type"]]) + inst["geom"] + inst["style"].to_bytes(3, "little")
    elif op == "font":
        return bytes([inst.get("opcode", ATTR), inst["font_id"]])
    elif op == "return":
        return bytes([RETURN])
    elif op == "text":
        tgt = inst.get("target_ptr", inst.get("target", 0))
        return bytes([TEXT, inst["x"], inst["y"]]) + tgt.to_bytes(3, "little")
    elif op == "inline_text":
        glyphs = inst.get("glyphs") or encode_glyphs(inst.get("text", ""))
        return bytes([INLINE_TEXT, inst["x"], inst["y"]]) + glyphs + b"\x00"
    raise ValueError(f"Unknown instruction operation: {op}")


def relocated(
    instructions: list[dict],
    mapping: dict[int, int],
    strings: dict[str, int] | None = None,
) -> list[dict]:
    """Return instructions with target pointers remapped to new flash addresses."""
    out = []
    strings = strings or {}
    for item in instructions:
        moved = dict(item)
        op = moved.get("op") or moved.get("opcode")
        if op == "text":
            tgt = moved.get("target_ptr", moved.get("target"))
            # 1. Try relocation by old flash address
            if tgt in mapping:
                new_tgt = mapping[tgt]
                moved["target_ptr"] = new_tgt
                if "target" in moved:
                    moved["target"] = new_tgt
            # 2. Or fallback to relocation by string match if available
            elif moved.get("text") and moved["text"] in strings:
                new_tgt = strings[moved["text"]]
                moved["target_ptr"] = new_tgt
                if "target" in moved:
                    moved["target"] = new_tgt
        out.append(moved)
    return out


def assemble(instructions: list[dict]) -> bytes:
    """Reciprocally compile an instruction list into bytecode."""
    return b"".join(encode_instruction(inst) for inst in instructions)
