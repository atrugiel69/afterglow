"""Section 10 Execution Object Compiler & Decompiler for Harmony 555 (Architecture 9)."""

from __future__ import annotations

import struct
from . import ahcm, command_index, layout, structures

BASE = ahcm.BASE

# Section 10 execution atom opcodes
DELAY = 0x7C
COMMAND = 0x7D
ENTER_SCREEN = 0x7E
OBJECT = 0x7F
ACTIVITY = 0x1F
SET_ACTIVITY_MODE = 0x90

# SmartState power opcodes (0x91..0x9B) <-> Device Index
POWER_OP_TO_DEV: dict[int, int] = {
    0x96: 0,
    0x99: 1,
    0x9B: 2,
    0x94: 3,
    0x97: 4,
    0x98: 5,
    0x93: 6,
    0x95: 7,
    0x91: 9,
}
DEV_TO_POWER_OP: dict[int, int] = {dev: op for op, dev in POWER_OP_TO_DEV.items()}

STARTUP_KEY_CODES = {0x01, 0xD4, 0xD5, 0xD6, 0xD7}


def _u24(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 3], "little")


def _iter_key_register_entries(payload: bytes):
    """Yield (kcode, operand, category, op_offset) across Section 06 & Section 09 registers."""
    # 1. Section 06 screen slot key registers
    sec6 = struct.unpack_from("<I", payload, 0x0C + 6 * 4)[0] - BASE
    if 0 <= sec6 + 2 <= len(payload):
        scr_cnt = struct.unpack_from("<H", payload, sec6)[0]
        for s in range(scr_cnt):
            tr_pos = sec6 + 3 + 3 * s
            if tr_pos + 3 > len(payload):
                break
            tr = _u24(payload, tr_pos) - BASE
            if not (0 <= tr + 6 <= len(payload)):
                continue
            slot_cnt = struct.unpack_from("<H", payload, tr + 4)[0]
            for sl in range(slot_cnt):
                sl_ptr_pos = tr + 6 + 3 * sl
                if sl_ptr_pos + 3 > len(payload):
                    break
                s_off = _u24(payload, sl_ptr_pos) - BASE
                if not (0 <= s_off + 6 <= len(payload)):
                    continue
                kreg_off = _u24(payload, s_off) - BASE
                if not (0 <= kreg_off < len(payload)):
                    continue
                cnt = payload[kreg_off]
                at, sz = (kreg_off + 2, 5) if cnt == 0 else (kreg_off + 1, 4)
                total = payload[kreg_off + 1] if cnt == 0 else cnt
                for idx in range(total):
                    rec_off = at + idx * sz
                    if rec_off + sz > len(payload):
                        break
                    kcode = payload[rec_off]
                    op_off = rec_off + (2 if sz == 5 else 1)
                    op, cat = struct.unpack_from("<HB", payload, op_off)
                    yield kcode, op, cat, op_off

    # 2. Section 09 mode key registers
    sec9 = struct.unpack_from("<I", payload, 0x0C + 9 * 4)[0] - BASE
    if 0 <= sec9 < len(payload):
        mode_cnt = payload[sec9]
        for m in range(mode_cnt):
            ptr_pos = sec9 + 1 + 3 * m
            if ptr_pos + 3 > len(payload):
                break
            kreg_off = _u24(payload, ptr_pos) - BASE
            if not (0 <= kreg_off < len(payload)):
                continue
            cnt = payload[kreg_off]
            at, sz = (kreg_off + 2, 5) if cnt == 0 else (kreg_off + 1, 4)
            total = payload[kreg_off + 1] if cnt == 0 else cnt
            for idx in range(total):
                rec_off = at + idx * sz
                if rec_off + sz > len(payload):
                    break
                kcode = payload[rec_off]
                op_off = rec_off + (2 if sz == 5 else 1)
                op, cat = struct.unpack_from("<HB", payload, op_off)
                yield kcode, op, cat, op_off


def _is_startup_object(atoms: tuple[tuple[int, int], ...]) -> bool:
    return (
        len(atoms) >= 5
        and atoms[0][1] == ENTER_SCREEN
        and atoms[1][1] == OBJECT
        and atoms[-3][1] == OBJECT
        and atoms[-2][1] == SET_ACTIVITY_MODE
        and atoms[-1][1] == OBJECT
    )


def _resolve_device_idx(dev: str | int, device_name_to_idx: dict[str, int] | None = None) -> int:
    if isinstance(dev, int):
        return dev
    if isinstance(dev, str):
        if device_name_to_idx and dev in device_name_to_idx:
            return device_name_to_idx[dev]
        if dev.isdigit():
            return int(dev)
    return 0


def _resolve_command_idx(
    dev_idx: int,
    cmd: str | int,
    command_name_lookup: dict[tuple[int, str], int] | None = None,
) -> int:
    if command_name_lookup and isinstance(cmd, str) and (dev_idx, cmd) in command_name_lookup:
        return command_name_lookup[(dev_idx, cmd)]
    if isinstance(cmd, str) and "Command_" in cmd:
        tail = cmd.split("_")[-1]
        if tail.isdigit():
            return int(tail)
    if isinstance(cmd, int):
        return cmd
    if isinstance(cmd, str) and cmd.isdigit():
        return int(cmd)
    return 0


def find_activity_startup_objects(
    payload: bytes,
    objs: list[dict] | None = None,
) -> dict[int, int]:
    """Map each activity root_screen index to its Section 10 startup object."""
    all_objs = objs if objs is not None else command_index.objects(payload)
    root_to_startup: dict[int, int] = {}

    def _collect_screens(obj_idx: int, depth: int = 4) -> list[int]:
        if depth <= 0 or not (0 <= obj_idx < len(all_objs)):
            return []
        screens: list[int] = []
        for op, code in all_objs[obj_idx]["atoms"]:
            if code == ENTER_SCREEN:
                screens.append(op)
            elif code == OBJECT:
                screens.extend(_collect_screens(op, depth - 1))
        return screens

    for obj in all_objs:
        atoms = obj["atoms"]
        if not _is_startup_object(atoms):
            continue
        screens = _collect_screens(atoms[-1][0])
        if screens:
            root_to_startup[screens[-1]] = obj["index"]

    return root_to_startup


def decompile_atoms_to_steps(
    objs: list[dict],
    atoms: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    cmd_idx_to_name: dict[tuple[int, int], str] | None = None,
    default_dev: str = "0",
    depth: int = 4,
) -> list[list]:
    """Decompile execution atoms (0x7D command, 0x7C delay, 0x7F nested) into macro steps."""
    steps: list[list] = []
    last_dev = default_dev

    def _walk(seq, cur_depth: int) -> None:
        nonlocal last_dev
        for operand, opcode in seq:
            if opcode == COMMAND:
                dev_idx = operand >> 8
                cmd_idx = operand & 0xFF
                last_dev = str(dev_idx)
                cmd_name = (
                    cmd_idx_to_name.get((dev_idx, cmd_idx), f"Command_{cmd_idx}")
                    if cmd_idx_to_name
                    else f"Command_{cmd_idx}"
                )
                steps.append(["command", last_dev, cmd_name])
            elif opcode == DELAY:
                steps.append(["delay", last_dev, int(operand)])
            elif opcode == OBJECT and cur_depth > 0 and 0 <= operand < len(objs):
                _walk(objs[operand]["atoms"], cur_depth - 1)

    _walk(atoms, depth)
    return steps


def decompile_startup_object(
    payload: bytes,
    startup_obj_idx: int,
    cmd_idx_to_name: dict[tuple[int, int], str] | None = None,
    objs: list[dict] | None = None,
) -> dict:
    """Decompile a Section 10 activity startup object into power lists and enter steps.

    Expected layout:
      [SCREEN(starting_id), OBJ#<on_list>, <setup atoms...>, OBJ#<off_list>,
       OP_90(slot), OBJ#<enter_root_screen>]
    """
    all_objs = objs if objs is not None else command_index.objects(payload)
    if not (0 <= startup_obj_idx < len(all_objs)):
        return {
            "startup_object": startup_obj_idx,
            "starting_screen": None,
            "on_list_obj": None,
            "off_list_obj": None,
            "op90_slot": None,
            "enter_root_obj": None,
            "power_on_devices": [],
            "power_off_devices": [],
            "enter": [],
        }

    atoms = all_objs[startup_obj_idx]["atoms"]
    starting_screen = atoms[0][0] if (atoms and atoms[0][1] == ENTER_SCREEN) else None
    on_list_obj = atoms[1][0] if (len(atoms) >= 2 and atoms[1][1] == OBJECT) else None
    off_list_obj = atoms[-3][0] if (len(atoms) >= 5 and atoms[-3][1] == OBJECT) else None
    op90_slot = atoms[-2][0] if (len(atoms) >= 5 and atoms[-2][1] == SET_ACTIVITY_MODE) else None
    enter_root_obj = atoms[-1][0] if (len(atoms) >= 5 and atoms[-1][1] == OBJECT) else None

    power_on: list[str] = []
    if on_list_obj is not None and 0 <= on_list_obj < len(all_objs):
        for val, pop_op in all_objs[on_list_obj]["atoms"]:
            if pop_op in POWER_OP_TO_DEV and val == 1:
                power_on.append(str(POWER_OP_TO_DEV[pop_op]))

    power_off: list[str] = []
    if off_list_obj is not None and 0 <= off_list_obj < len(all_objs):
        for val, pop_op in all_objs[off_list_obj]["atoms"]:
            if pop_op in POWER_OP_TO_DEV and val == 0:
                power_off.append(str(POWER_OP_TO_DEV[pop_op]))

    setup_atoms = atoms[2:-3] if len(atoms) >= 5 else ()
    default_dev = power_on[0] if power_on else "0"
    enter_steps = decompile_atoms_to_steps(
        all_objs,
        setup_atoms,
        cmd_idx_to_name=cmd_idx_to_name,
        default_dev=default_dev,
    )

    return {
        "startup_object": startup_obj_idx,
        "starting_screen": starting_screen,
        "on_list_obj": on_list_obj,
        "off_list_obj": off_list_obj,
        "op90_slot": op90_slot,
        "enter_root_obj": enter_root_obj,
        "power_on_devices": power_on,
        "power_off_devices": power_off,
        "enter": enter_steps,
    }


def decompile_activity_leave(
    payload: bytes,
    trigger_obj_idx: int | None,
    cmd_idx_to_name: dict[tuple[int, int], str] | None = None,
    default_dev: str = "0",
    objs: list[dict] | None = None,
) -> tuple[list[list], list[str]]:
    """Decompile an activity's shutdown/leave steps and off-list opcodes from its trigger."""
    all_objs = objs if objs is not None else command_index.objects(payload)
    if trigger_obj_idx is None or not (0 <= trigger_obj_idx < len(all_objs)):
        return [], []

    macro_atoms: list[tuple[int, int]] = []
    power_off: list[str] = []

    def _collect(seq, depth: int = 3) -> None:
        for operand, opcode in seq:
            if opcode in (ACTIVITY, ENTER_SCREEN, SET_ACTIVITY_MODE):
                continue
            if opcode in POWER_OP_TO_DEV and operand == 0:
                power_off.append(str(POWER_OP_TO_DEV[opcode]))
            elif opcode in (COMMAND, DELAY):
                macro_atoms.append((operand, opcode))
            elif opcode == OBJECT and depth > 0 and 0 <= operand < len(all_objs):
                _collect(all_objs[operand]["atoms"], depth - 1)

    _collect(all_objs[trigger_obj_idx]["atoms"])
    leave_steps = decompile_atoms_to_steps(
        all_objs,
        macro_atoms,
        cmd_idx_to_name=cmd_idx_to_name,
        default_dev=default_dev,
    )
    return leave_steps, power_off


def lower_power_list(
    device_ids: list,
    state_bit: int,
    device_name_to_idx: dict[str, int] | None = None,
) -> list[tuple[int, int]]:
    """Lower an ordered list of device IDs into SmartState power atoms (state_bit, 0x91..0x9B)."""
    atoms: list[tuple[int, int]] = []
    for dev in device_ids or ():
        dev_idx = _resolve_device_idx(dev, device_name_to_idx)
        pop_op = DEV_TO_POWER_OP.get(dev_idx)
        if pop_op is not None:
            atoms.append((state_bit & 0xFFFF, pop_op))
    return atoms


def lower_macro_steps(
    steps: list,
    device_name_to_idx: dict[str, int] | None = None,
    command_name_lookup: dict[tuple[int, str], int] | None = None,
) -> list[tuple[int, int]]:
    """Lower declarative Afterglow macro steps into Architecture 9 (operand, opcode) atoms."""
    atoms: list[tuple[int, int]] = []
    for step in steps or ():
        if isinstance(step, dict):
            stype = step.get("type")
            if stype == "command":
                dev_idx = _resolve_device_idx(step.get("device", 0), device_name_to_idx)
                cmd_idx = _resolve_command_idx(
                    dev_idx, step.get("command", 0), command_name_lookup
                )
                atoms.append((((dev_idx & 0xFF) << 8) | (cmd_idx & 0xFF), COMMAND))
            elif stype == "delay":
                ms = max(0, min(0xFFFF, int(step.get("ms", 0))))
                atoms.append((ms, DELAY))
            elif stype in ("object", "macro"):
                atoms.append((int(step.get("object", 0)) & 0xFFFF, OBJECT))
            elif stype == "screen":
                atoms.append((int(step.get("screen", 0)) & 0xFFFF, ENTER_SCREEN))
            continue

        if not isinstance(step, (list, tuple)) or len(step) < 2:
            continue

        if isinstance(step[0], int) and isinstance(step[1], int) and len(step) == 2:
            atoms.append((step[0] & 0xFFFF, step[1] & 0xFF))
            continue

        kind = str(step[0])
        if kind == "command" and len(step) >= 3:
            dev_idx = _resolve_device_idx(step[1], device_name_to_idx)
            cmd_idx = _resolve_command_idx(dev_idx, step[2], command_name_lookup)
            atoms.append((((dev_idx & 0xFF) << 8) | (cmd_idx & 0xFF), COMMAND))
        elif kind == "delay":
            ms_raw = step[2] if len(step) >= 3 else step[1]
            ms = max(0, min(0xFFFF, int(ms_raw)))
            atoms.append((ms, DELAY))
        elif kind in ("input", "state") and len(step) >= 3:
            dev_idx = _resolve_device_idx(step[1], device_name_to_idx)
            val = str(step[3] if (kind == "state" and len(step) >= 4) else step[2])
            cmd_idx = _resolve_command_idx(dev_idx, val, command_name_lookup)
            if (
                cmd_idx == 0
                and command_name_lookup
                and (dev_idx, val) not in command_name_lookup
                and (dev_idx, f"Input{val}") in command_name_lookup
            ):
                cmd_idx = command_name_lookup[(dev_idx, f"Input{val}")]
            atoms.append((((dev_idx & 0xFF) << 8) | (cmd_idx & 0xFF), COMMAND))
        elif kind == "number" and len(step) >= 3:
            dev_idx = _resolve_device_idx(step[1], device_name_to_idx)
            for ch in str(step[2]):
                if not ch.isdigit():
                    continue
                if command_name_lookup and (dev_idx, f"Number{ch}") in command_name_lookup:
                    cmd_idx = command_name_lookup[(dev_idx, f"Number{ch}")]
                elif command_name_lookup and (dev_idx, ch) in command_name_lookup:
                    cmd_idx = command_name_lookup[(dev_idx, ch)]
                else:
                    continue
                atoms.append((((dev_idx & 0xFF) << 8) | (cmd_idx & 0xFF), COMMAND))
        elif kind in ("object", "macro"):
            atoms.append((int(step[1]) & 0xFFFF, OBJECT))
        elif kind == "screen":
            atoms.append((int(step[1]) & 0xFFFF, ENTER_SCREEN))

    return atoms


class ObjectAllocator:
    """Append-only Section 10 execution object allocator preserving donor indices 0..1286."""

    def __init__(self, lay: layout.Layout, sec10_bytes: bytes):
        self.lay = lay
        self._orig_bytes = sec10_bytes
        self.orig_count = struct.unpack_from("<H", sec10_bytes, 0)[0]
        self.count = self.orig_count
        self.ptr_table = bytearray(sec10_bytes[2 : 2 + 3 * self.orig_count])
        self.modified = False

    def allocate(self, atoms: list[tuple[int, int]]) -> int:
        """Write [atom_count, (op_u16, code_u8)*] to lay.out and append pointer to Section 10."""
        body = structures.object_body(atoms)
        offset = layout.place(self.lay.out, body)
        self.ptr_table.extend(structures.pointer24(offset))
        obj_idx = self.count
        self.count += 1
        self.modified = True
        return obj_idx

    def replace(self, obj_idx: int, atoms: list[tuple[int, int]]) -> None:
        """Write new atoms to lay.out and repoint an existing Section 10 index without shifting."""
        if not (0 <= obj_idx < self.count):
            raise IndexError(f"Section 10 object index {obj_idx} out of range")
        body = structures.object_body(atoms)
        offset = layout.place(self.lay.out, body)
        self.ptr_table[3 * obj_idx : 3 * obj_idx + 3] = structures.pointer24(offset)
        self.modified = True

    def to_bytes(self) -> bytes:
        """Serialize Section 10 pointer table (exact donor bytes if unmodified)."""
        if not self.modified:
            return self._orig_bytes
        return struct.pack("<H", self.count) + bytes(self.ptr_table)


def compile_activity_startup(
    obj_alloc: ObjectAllocator,
    donor_startup: dict,
    enter_steps: list,
    power_on_devices: list,
    power_off_devices: list,
    device_name_to_idx: dict[str, int] | None = None,
    command_name_lookup: dict[tuple[int, str], int] | None = None,
) -> int:
    """Allocate new <on_list>, <off_list>, and startup object in Section 10; return new index."""
    on_atoms = lower_power_list(power_on_devices, 1, device_name_to_idx=device_name_to_idx)
    off_atoms = lower_power_list(power_off_devices, 0, device_name_to_idx=device_name_to_idx)
    setup_atoms = lower_macro_steps(
        enter_steps,
        device_name_to_idx=device_name_to_idx,
        command_name_lookup=command_name_lookup,
    )

    on_obj_idx = obj_alloc.allocate(on_atoms)
    off_obj_idx = obj_alloc.allocate(off_atoms)

    startup_atoms: list[tuple[int, int]] = []
    if donor_startup.get("starting_screen") is not None:
        startup_atoms.append((donor_startup["starting_screen"], ENTER_SCREEN))
    startup_atoms.append((on_obj_idx, OBJECT))
    startup_atoms.extend(setup_atoms)
    startup_atoms.append((off_obj_idx, OBJECT))
    if donor_startup.get("op90_slot") is not None:
        startup_atoms.append((donor_startup["op90_slot"], SET_ACTIVITY_MODE))
    if donor_startup.get("enter_root_obj") is not None:
        startup_atoms.append((donor_startup["enter_root_obj"], OBJECT))

    return obj_alloc.allocate(startup_atoms)


def patch_activities_menu_startup_obj(
    lay: layout.Layout,
    donor_payload: bytes,
    old_obj_idx: int,
    new_obj_idx: int,
) -> bool:
    """Patch key-register pointers in lay.out from old_obj_idx to new_obj_idx."""
    patched = False
    for kcode, op, cat, op_off in _iter_key_register_entries(donor_payload):
        if kcode in STARTUP_KEY_CODES and cat == OBJECT and op == old_obj_idx:
            struct.pack_into("<H", lay.out, op_off, new_obj_idx)
            patched = True

    sec9 = struct.unpack_from("<I", donor_payload, 0x0C + 9 * 4)[0] - BASE
    if 0 <= sec9 < len(donor_payload):
        mode_cnt = donor_payload[sec9]
        heap_ptrs = [
            _u24(donor_payload, sec9 + 1 + 3 * m) - BASE
            for m in range(mode_cnt)
            if sec9 + 4 + 3 * m <= len(donor_payload)
        ]
        valid_ptrs = [p for p in heap_ptrs if 0 <= p < sec9]
        if valid_ptrs:
            needle = struct.pack("<BHB", 0x01, old_obj_idx, OBJECT)
            pos = min(valid_ptrs)
            while True:
                hit = donor_payload.find(needle, pos, sec9)
                if hit == -1:
                    break
                struct.pack_into("<H", lay.out, hit + 1, new_obj_idx)
                patched = True
                pos = hit + 1

    return patched


def compile_activity_leave(
    obj_alloc: ObjectAllocator,
    trigger_obj_idx: int,
    act_id: int,
    leave_steps: list,
    power_off_devices: list | None = None,
    device_name_to_idx: dict[str, int] | None = None,
    command_name_lookup: dict[tuple[int, str], int] | None = None,
) -> None:
    """Compile activity leave steps into Section 10 and link via the activity trigger object."""
    trig_atoms: list[tuple[int, int]] = [(0xFF00 | (act_id & 0xFF), ACTIVITY)]

    if power_off_devices:
        off_atoms = lower_power_list(
            power_off_devices, 0, device_name_to_idx=device_name_to_idx
        )
        if off_atoms:
            off_obj_idx = obj_alloc.allocate(off_atoms)
            trig_atoms.append((off_obj_idx, OBJECT))

    leave_atoms = lower_macro_steps(
        leave_steps,
        device_name_to_idx=device_name_to_idx,
        command_name_lookup=command_name_lookup,
    )
    if leave_atoms:
        leave_obj_idx = obj_alloc.allocate(leave_atoms)
        trig_atoms.append((leave_obj_idx, OBJECT))

    obj_alloc.replace(trigger_obj_idx, trig_atoms)
