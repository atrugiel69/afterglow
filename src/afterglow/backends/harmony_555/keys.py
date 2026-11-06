"""The Harmony 555's physical keys: which scan code each key sends.

A project names keys the way every Harmony remote's software does - `VolumeUp`,
`DirectionLeft`, `Number7` - in a device command's hard slot and an activity's
`hard_macros`. The configuration binds them by scan code: a context or a mode list row
for key `0x80 | scan`.

On Architecture 9 (Mocha Grande), scan codes follow an 8x8 matrix addressing scheme:
`scan = (row << 3) | column`.
"""

from __future__ import annotations

SCANS = {
    # Number pad
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
    "Clear": 19,
    "NumberEnter": 25,

    # Transport controls
    "Play": 4,
    "Pause": 12,
    "Stop": 7,
    "Record": 15,
    "Rewind": 13,
    "FastForward": 14,
    "SkipBack": 5,
    "SkipForward": 6,

    # Navigation & D-pad
    "DirectionUp": 45,
    "DirectionDown": 49,
    "DirectionLeft": 53,
    "DirectionRight": 44,
    "Select": 52,
    "Menu": 28,
    "Exit": 29,
    "Info": 20,
    "Guide": 21,

    # Volume & Channel
    "VolumeUp": 51,
    "VolumeDown": 50,
    "VolumeMute": 61,
    "ChannelUp": 57,
    "ChannelDown": 41,
    "PrevChannel": 58,

    # Teletext / Color buttons
    "Red": 27,
    "Green": 35,
    "Yellow": 34,
    "Blue": 33,

    # Screen paging & dedicated navigation
    "UpArrow": 36,
    "DownArrow": 37,

    # Flow keys owned by the firmware
    "Activities": 30,
    "Off": 31,
    "Help": 32,
}

ACTIVITIES, OFF, HELP = SCANS["Activities"], SCANS["Off"], SCANS["Help"]

FLOW = (ACTIVITIES, OFF, HELP)
COMMAND_KEYS = tuple(name for name, scan in SCANS.items() if scan not in FLOW)

_OWN_NAMES = {
    **{f"Number{digit}": (str(digit),) for digit in range(10)},
    "VolumeMute": ("Mute",),
    "NumberEnter": ("Enter",),
    "PrevChannel": ("Prev",),
}


def is_own_command(key: str, command: str) -> bool:
    """Whether ``command`` is the primary command mapped to ``key``."""
    from ...project_devices import KEY_STAND_INS

    return (
        command == key
        or command in _OWN_NAMES.get(key, ())
        or command in KEY_STAND_INS.get(key, ())
    )


def code(scan: int) -> int:
    """The key code a list or context row binds a scan code by."""
    return 0x80 | scan


def by_name(name: str) -> int | None:
    """The key code of a named key, or None if unrecognized."""
    scan = SCANS.get(name)
    return None if scan is None else code(scan)


def name_of(key_code: int) -> str | None:
    """The name of the key bound by ``key_code``."""
    if not key_code & 0x80:
        return None
    return _NAMES.get(key_code & 0x7F)


_NAMES = {scan: name for name, scan in SCANS.items()}
