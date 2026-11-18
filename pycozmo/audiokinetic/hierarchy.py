"""

AudioKinetic Wwise sound bank hierarchy (HIRC) reader, bank version 120.

Reads the objects needed to resolve an event to media: events, actions, sounds,
random/sequence, switch and layer containers, actor-mixers, with their parent,
properties and randomizer ranges, and the media embedded in banks (DIDX/DATA).

Each object of a supported type is read completely; an object that does not end
exactly where the bank says is reported as unreadable instead of being trusted.
Music objects, buses, effects and attenuations are kept as ``Node`` with their type only.

Specification: ``docs/specs/wwise-banks.md``.

"""

import dataclasses
import pathlib
import struct
from typing import Dict, Iterable, List, Optional, Tuple, Union

from . import exception
from ..logger import logger


__all__ = [
    "SUPPORTED_VERSION",
    "PROP_VOLUME",
    "PROP_PITCH",
    "PROP_LPF",
    "PROP_HPF",
    "PROP_MAKE_UP_GAIN",
    "PROP_DELAY_TIME",
    "PROP_TRANSITION_TIME",
    "PROP_PROBABILITY",
    "PROP_LOOP",
    "PROP_INITIAL_DELAY",

    "ACTION_STOP",
    "ACTION_PLAY",
    "ACTION_SEEK",
    "SCOPE_ELEMENT",
    "SCOPE_ALL",
    "SCOPE_ALL_EXCEPT",

    "Node",
    "Sound",
    "RanSeqContainer",
    "SwitchContainer",
    "LayerContainer",
    "ActorMixer",
    "Action",
    "Event",
    "EmbeddedMedia",
    "Hierarchy",
]


SUPPORTED_VERSION = 120

# Property identifiers of bank versions 114 to 126.
PROP_VOLUME = 0x00              # dB
PROP_PITCH = 0x02               # cents
PROP_LPF = 0x03                 # 0-100
PROP_HPF = 0x04                 # 0-100
PROP_MAKE_UP_GAIN = 0x06        # dB
PROP_DELAY_TIME = 0x0F          # ms, actions
PROP_TRANSITION_TIME = 0x10     # ms, fade of actions
PROP_PROBABILITY = 0x11         # percent
PROP_LOOP = 0x3A                # loop count, 0: infinite
PROP_INITIAL_DELAY = 0x3B       # s
# Properties holding integers; the others hold floats.
_INT_PROPS = {PROP_DELAY_TIME, PROP_TRANSITION_TIME, PROP_LOOP} | set(range(0x2D, 0x36)) | {0x37, 0x38, 0x39}

# Action types (high byte of the 16-bit action type) and scopes (low byte).
ACTION_STOP = 0x01
ACTION_PLAY = 0x04
ACTION_SEEK = 0x1E
SCOPE_ELEMENT = (0x02, 0x03)
SCOPE_ALL = (0x04, 0x05)
SCOPE_ALL_EXCEPT = (0x08, 0x09)

_HIRC_SOUND = 2
_HIRC_ACTION = 3
_HIRC_EVENT = 4
_HIRC_RANSEQ = 5
_HIRC_SWITCH = 6
_HIRC_ACTOR_MIXER = 7
_HIRC_LAYER = 9

Value = Union[int, float]


@dataclasses.dataclass
class Node:
    """ Hierarchy object. ``props`` and ``ranges`` (randomizer min, max) are keyed by property ID. """
    id: int
    type: int
    bank: str = ""
    parent_id: int = 0
    props: Dict[int, Value] = dataclasses.field(default_factory=dict)
    ranges: Dict[int, Tuple[Value, Value]] = dataclasses.field(default_factory=dict)
    children: Tuple[int, ...] = ()


@dataclasses.dataclass
class Sound(Node):
    source_id: int = 0
    stream_type: int = 0
    plugin_id: int = 0
    media_size: int = 0


@dataclasses.dataclass
class RanSeqContainer(Node):
    loop_count: int = 1             # 0: infinite
    avoid_repeat_count: int = 0
    random_mode: int = 0            # 0: standard, 1: shuffle
    mode: int = 0                   # 0: random, 1: sequence
    playlist: Tuple[Tuple[int, int], ...] = ()      # (child ID, weight)


@dataclasses.dataclass
class SwitchContainer(Node):
    group_type: int = 0
    group_id: int = 0
    default_switch: int = 0
    switches: Dict[int, Tuple[int, ...]] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class LayerContainer(Node):
    pass


@dataclasses.dataclass
class ActorMixer(Node):
    pass


@dataclasses.dataclass
class Action:
    id: int
    action_type: int                # ACTION_*
    scope: int
    target_id: int
    is_bus: bool = False
    bank: str = ""
    props: Dict[int, Value] = dataclasses.field(default_factory=dict)
    ranges: Dict[int, Tuple[Value, Value]] = dataclasses.field(default_factory=dict)
    exceptions: Tuple[int, ...] = ()
    # Seek parameters.
    seek_relative: bool = False
    seek_value: float = 0.0
    seek_min: float = 0.0
    seek_max: float = 0.0


@dataclasses.dataclass
class Event:
    id: int
    action_ids: Tuple[int, ...]
    bank: str = ""


@dataclasses.dataclass(frozen=True)
class EmbeddedMedia:
    bank_path: pathlib.Path
    offset: int                     # in the file
    size: int


class _Reader:
    __slots__ = ("data", "pos", "end")

    def __init__(self, data: bytes, pos: int = 0, end: Optional[int] = None) -> None:
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end

    def _take(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.pos + size > self.end:
            raise exception.AudioKineticFormatError("Object shorter than expected.")
        value = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return value

    def u8(self) -> int:
        return self._take("<B")[0]

    def u16(self) -> int:
        return self._take("<H")[0]

    def u32(self) -> int:
        return self._take("<I")[0]

    def s32(self) -> int:
        return self._take("<i")[0]

    def f32(self) -> float:
        return self._take("<f")[0]

    def var(self) -> int:
        """ Big-endian base-128 integer. """
        value = 0
        for _ in range(5):
            b = self.u8()
            value = (value << 7) | (b & 0x7f)
            if not b & 0x80:
                return value
        raise exception.AudioKineticFormatError("Invalid variable-length integer.")

    def skip(self, n: int) -> None:
        if self.pos + n > self.end:
            raise exception.AudioKineticFormatError("Object shorter than expected.")
        self.pos += n

    def ids(self, n: int) -> Tuple[int, ...]:
        return self._take("<{}I".format(n)) if n else ()


def _prop_value(r: _Reader, prop_id: int) -> Value:
    return r.s32() if prop_id in _INT_PROPS else r.f32()


def _read_props(r: _Reader) -> Tuple[Dict[int, Value], Dict[int, Tuple[Value, Value]]]:
    count = r.u8()
    ids = [r.u8() for _ in range(count)]
    props = {i: _prop_value(r, i) for i in ids}
    count = r.u8()
    ids = [r.u8() for _ in range(count)]
    ranges = {i: (_prop_value(r, i), _prop_value(r, i)) for i in ids}
    return props, ranges


def _skip_rtpc(r: _Reader) -> None:
    for _ in range(r.u16()):
        r.u32()                             # RTPC ID
        r.u8()                              # type
        r.u8()                              # accumulation
        r.var()                             # parameter ID
        r.u32()                             # curve ID
        r.u8()                              # scaling
        r.skip(r.u16() * 12)                # graph points


def _read_exceptions(r: _Reader) -> Tuple[int, ...]:
    ids = []
    for _ in range(r.u32()):
        ids.append(r.u32())
        r.u8()                              # is bus
    return tuple(ids)


def _read_node_base(r: _Reader, node: Node, feedback: bool) -> None:
    # Effects.
    r.u8()                                  # bIsOverrideParentFX
    num_fx = r.u8()
    if num_fx:
        r.u8()                              # bitsFXBypass
        r.skip(num_fx * 7)                  # index, fx ID, is share set, is rendered
    r.u8()                                  # bOverrideAttachmentParams
    r.u32()                                 # OverrideBusId
    node.parent_id = r.u32()
    r.u8()                                  # priority and MIDI flags
    node.props, node.ranges = _read_props(r)
    # Positioning.
    bits = r.u8()
    if bits & 0x01 and bits & 0x08:         # override parent, 3D available
        bits3d = r.u8()
        r.u32()                             # attenuation ID
        if bits3d & 0x03 != 1:              # automation
            r.u8()                          # path mode
            r.s32()                         # transition time
            r.skip(r.u32() * 16)            # vertices
            items = r.u32()
            r.skip(items * 8)               # playlist items
            r.skip(items * 12)              # automation ranges
    # Aux sends.
    if r.u8() & 0x08:
        r.skip(16)
    # Advanced settings.
    r.skip(6)
    # States.
    for _ in range(r.u32()):
        r.u32()
        r.u8()
        r.skip(r.u16() * 8)
    _skip_rtpc(r)
    if feedback:
        r.u32()                             # feedback bus


def _read_children(r: _Reader) -> Tuple[int, ...]:
    return r.ids(r.u32())


def _read_object(obj_type: int, obj_id: int, r: _Reader, feedback: bool) -> object:
    if obj_type == _HIRC_EVENT:
        return Event(obj_id, r.ids(r.u32()))
    if obj_type == _HIRC_ACTION:
        action_type = r.u16()
        a = Action(obj_id, action_type >> 8, action_type & 0xff, r.u32())
        a.is_bus = bool(r.u8() & 1)
        a.props, a.ranges = _read_props(r)
        if a.action_type == ACTION_PLAY:
            r.u8()                          # fade curve
            r.u32()                         # bank ID
        elif a.action_type == ACTION_STOP:
            r.u8()                          # fade curve
            a.exceptions = _read_exceptions(r)
        elif a.action_type == ACTION_SEEK:
            a.seek_relative = bool(r.u8())
            a.seek_value, a.seek_min, a.seek_max = r.f32(), r.f32(), r.f32()
            r.u8()                          # snap to marker
            a.exceptions = _read_exceptions(r)
        else:
            r.pos = r.end                   # parameters of other actions are not needed
        return a
    if obj_type == _HIRC_SOUND:
        s = Sound(obj_id, obj_type)
        s.plugin_id = r.u32()
        s.stream_type = r.u8()
        s.source_id = r.u32()
        s.media_size = r.u32()
        r.u8()                              # source bits
        if s.plugin_id & 0x0f in (2, 5):
            r.skip(r.u32())                 # plugin parameters
        _read_node_base(r, s, feedback)
        return s
    if obj_type == _HIRC_RANSEQ:
        c = RanSeqContainer(obj_id, obj_type)
        _read_node_base(r, c, feedback)
        c.loop_count = r.u16()
        r.u16()
        r.u16()                             # loop modifiers
        r.f32()
        r.f32()
        r.f32()                             # transition time and modifiers
        c.avoid_repeat_count = r.u16()
        r.u8()                              # transition mode
        c.random_mode = r.u8()
        c.mode = r.u8()
        r.u8()                              # flags
        c.children = _read_children(r)
        c.playlist = tuple((r.u32(), r.s32()) for _ in range(r.u16()))
        return c
    if obj_type == _HIRC_SWITCH:
        c = SwitchContainer(obj_id, obj_type)
        _read_node_base(r, c, feedback)
        c.group_type = r.u8()
        c.group_id = r.u32()
        c.default_switch = r.u32()
        r.u8()                              # continuous validation
        c.children = _read_children(r)
        for _ in range(r.u32()):
            switch_id = r.u32()
            c.switches[switch_id] = r.ids(r.u32())
        r.skip(r.u32() * 14)                # node parameters
        return c
    if obj_type == _HIRC_LAYER:
        c = LayerContainer(obj_id, obj_type)
        _read_node_base(r, c, feedback)
        c.children = _read_children(r)
        for _ in range(r.u32()):            # layers
            r.u32()                         # layer ID
            _skip_rtpc(r)
            r.u32()                         # crossfade parameter
            r.u8()                          # crossfade parameter type
            for _ in range(r.u32()):        # associated children
                r.u32()
                r.skip(r.u32() * 12)        # crossfade curve
        r.u8()                              # continuous validation
        return c
    if obj_type == _HIRC_ACTOR_MIXER:
        c = ActorMixer(obj_id, obj_type)
        _read_node_base(r, c, feedback)
        c.children = _read_children(r)
        return c
    r.pos = r.end
    return Node(obj_id, obj_type)


class Hierarchy:
    """ Objects of one or more sound banks. """

    def __init__(self) -> None:
        self.objects = {}       # type: Dict[int, object]
        self.media = {}         # type: Dict[int, EmbeddedMedia]
        self.unreadable = {}    # type: Dict[int, str]
        self.banks = []         # type: List[pathlib.Path]

    @classmethod
    def load(cls, paths: Iterable[Union[str, pathlib.Path]]) -> "Hierarchy":
        h = cls()
        for path in paths:
            h.add_bank(pathlib.Path(path))
        return h

    def add_bank(self, path: pathlib.Path) -> None:
        data = path.read_bytes()
        pos = 0
        version = None
        feedback = False
        data_offset = None
        didx = []
        hirc = None
        while pos + 8 <= len(data):
            tag = data[pos:pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            start, end = pos + 8, pos + 8 + size
            if end > len(data):
                raise exception.AudioKineticFormatError("{}: truncated {} chunk.".format(path.name, tag))
            if tag == b"BKHD":
                version = struct.unpack_from("<I", data, start)[0]
                if version != SUPPORTED_VERSION:
                    raise exception.AudioKineticFormatError(
                        "{}: bank version {} is not supported (expected {}).".format(
                            path.name, version, SUPPORTED_VERSION))
                feedback = bool(struct.unpack_from("<I", data, start + 12)[0] & 1)
            elif tag == b"DIDX":
                didx = [struct.unpack_from("<III", data, start + 12 * i) for i in range(size // 12)]
            elif tag == b"DATA":
                data_offset = start
            elif tag == b"HIRC":
                hirc = (start, end)
            pos = end
        if version is None:
            raise exception.AudioKineticFormatError("{}: not a sound bank.".format(path.name))
        self.banks.append(path)
        if data_offset is not None:
            for media_id, offset, size in didx:
                self.media.setdefault(media_id, EmbeddedMedia(path, data_offset + offset, size))
        if hirc is None:
            return
        start, end = hirc
        count = struct.unpack_from("<I", data, start)[0]
        pos = start + 4
        for _ in range(count):
            if pos + 9 > end:
                raise exception.AudioKineticFormatError("{}: truncated HIRC chunk.".format(path.name))
            obj_type, size, obj_id = struct.unpack_from("<BII", data, pos)
            obj_end = pos + 5 + size
            r = _Reader(data, pos + 9, obj_end)
            try:
                obj = _read_object(obj_type, obj_id, r, feedback)
                if r.pos != obj_end:
                    raise exception.AudioKineticFormatError(
                        "{} bytes left".format(obj_end - r.pos))
            except exception.AudioKineticFormatError as e:
                self.unreadable[obj_id] = "type {}: {}".format(obj_type, e)
                logger.debug("%s: unreadable object %d of type %d: %s", path.name, obj_id, obj_type, e)
                obj = Node(obj_id, obj_type)
            obj.bank = path.name
            self.objects.setdefault(obj_id, obj)
            pos = obj_end

    def get(self, obj_id: int):
        return self.objects.get(obj_id)

    def parents(self, node: Node) -> List[Node]:
        """ The node and its ancestors, nearest first. """
        chain = []
        seen = set()
        while node is not None and node.id not in seen:
            chain.append(node)
            seen.add(node.id)
            parent = self.objects.get(node.parent_id) if node.parent_id else None
            node = parent if isinstance(parent, Node) else None
        return chain
