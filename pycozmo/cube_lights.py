"""

Light patterns of the cubes: reading, conversion to cube packets and playback (docs/specs/cube-light-patterns.md).

"""

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import protocol_encoder
from .json_loader import get_json_files, load_json_file
from .logger import logger


__all__ = [
    "FRAME_MS",
    "LightPattern",
    "LightAnimation",
    "CubeLightPlayer",

    "load_cube_light_animations",
    "load_cube_animation_triggers",
    "ms_to_frames",
    "offset_to_frames",
    "white_balance",
    "encode_color",
    "pattern_to_light_states",
]


#: Duration of a frame of the cube, in milliseconds.
FRAME_MS = 30
#: Period of the LEDs that never change.
SOLID_PERIOD = 0x7fffffff

Color = tuple[int, int, int, int]


@dataclass
class LightPattern:
    """ The pattern of the four LEDs of a cube, and how long it plays in an animation. """
    on_colors: list[Color]
    off_colors: list[Color]
    on_period_ms: list[int]
    off_period_ms: list[int]
    transition_on_ms: list[int]
    transition_off_ms: list[int]
    offset: list[int]
    rotation_period_ms: int
    duration_ms: int = 0
    can_be_overridden: bool = True
    name: str = ""

    @classmethod
    def from_json(cls, entry: dict[str, Any], name: str = "") -> "LightPattern":
        """ Read an entry of an animation. Raises ValueError when a field is missing. """
        if 'pattern' not in entry or 'duration_ms' not in entry:
            raise ValueError("Missing member field")
        data = entry['pattern']
        try:
            return cls(
                on_colors=[_color(c) for c in _four(data['onColors'])],
                off_colors=[_color(c) for c in _four(data['offColors'])],
                on_period_ms=[int(v) for v in _four(data['onPeriod_ms'])],
                off_period_ms=[int(v) for v in _four(data['offPeriod_ms'])],
                transition_on_ms=[int(v) for v in _four(data['transitionOnPeriod_ms'])],
                transition_off_ms=[int(v) for v in _four(data['transitionOffPeriod_ms'])],
                offset=[int(v) for v in _four(data['offset'])],
                rotation_period_ms=int(data['rotationPeriod_ms']),
                duration_ms=int(entry['duration_ms']),
                can_be_overridden=bool(entry.get('canBeOverridden', True)),
                name=str(entry.get('patternDebugName', name)))
        except (KeyError, TypeError) as e:
            raise ValueError("Invalid pattern: {}".format(e)) from e


def _four(values: Sequence[Any]) -> Sequence[Any]:
    if len(values) != 4:
        raise ValueError("Expected 4 values, got {}".format(len(values)))
    return values


def _color(values: Sequence[Any]) -> Color:
    if len(values) != 4:
        raise ValueError("Expected 4 color values, got {}".format(len(values)))
    r, g, b, a = (int(v) for v in values)
    return r, g, b, a


@dataclass
class LightAnimation:
    """ A sequence of patterns. """
    name: str
    patterns: list[LightPattern] = field(default_factory=list)

    @property
    def can_be_overridden(self) -> bool:
        """ Whether another animation may replace this one: the flag of its first pattern. """
        return self.patterns[0].can_be_overridden if self.patterns else True


def ms_to_frames(ms: int) -> int:
    """ Milliseconds to frames of the cube: rounded up, on 32 bits then kept on 8 bits; -1 gives 255. """
    if ms == -1:
        return 0xff
    return (((ms + FRAME_MS - 1) & 0xffffffff) // FRAME_MS) & 0xff


def offset_to_frames(ms: int) -> int:
    """ Same as ms_to_frames for the offset of a LED: signed, kept on 16 bits. """
    if ms == -1:
        return 0xff
    value = ms + FRAME_MS - 1
    frames = abs(value) // FRAME_MS * (1 if value >= 0 else -1)
    frames &= 0xffff
    return frames - 0x10000 if frames >= 0x8000 else frames


def white_balance(color: Color) -> Color:
    """ Green and blue are scaled down to 0.6 when the red value is not 0. """
    r, g, b, a = color
    if r > 0:
        inv = np.float32(0.003921569)
        scale = np.float32(0.6)
        full = np.float32(255.0)
        g = int(np.float32(np.float32(np.float32(g) * inv) * scale) * full)
        b = int(np.float32(np.float32(np.float32(b) * inv) * scale) * full)
    return r, g, b, a


def encode_color(color: Color) -> int:
    """ 16-bit color of the cube: 5 bits per channel after the white balance, bit 15 when the alpha is not 0. """
    r, g, b, a = white_balance(color)
    value = ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)
    if a != 0:
        value |= 0x8000
    return value


def pattern_to_light_states(pattern: LightPattern) -> tuple[tuple[protocol_encoder.LightState, ...], int]:
    """ The four LightState of the pattern and the rotation period, in frames, for the selection of the cube. """
    states = []
    for i in range(4):
        on_color, off_color = pattern.on_colors[i], pattern.off_colors[i]
        on_period, off_period = pattern.on_period_ms[i], pattern.off_period_ms[i]
        if on_period == 0:
            if off_period == 0:
                on_color = off_color = (0, 0, 0, 0)
                off_period = SOLID_PERIOD
            else:
                on_color = off_color
            on_period = SOLID_PERIOD
        elif off_period == 0:
            off_color = on_color
            off_period = SOLID_PERIOD
        states.append(protocol_encoder.LightState(
            on_color=encode_color(on_color),
            off_color=encode_color(off_color),
            on_frames=ms_to_frames(on_period),
            off_frames=ms_to_frames(off_period),
            transition_on_frames=ms_to_frames(pattern.transition_on_ms[i]),
            transition_off_frames=ms_to_frames(pattern.transition_off_ms[i]),
            offset=offset_to_frames(pattern.offset[i])))
    return tuple(states), ms_to_frames(pattern.rotation_period_ms)


def load_cube_light_animations(resource_dir: str) -> dict[str, LightAnimation]:
    """ Load the animations of the cube lights. Files with an invalid pattern are skipped. """
    start_time = time.perf_counter()
    files = get_json_files(
        resource_dir, [os.path.join('cozmo_resources', 'config', 'engine', 'lights', 'cubeLights')])
    animations: dict[str, LightAnimation] = {}
    for filename in sorted(files):
        data = load_json_file(filename)
        if not data:
            logger.error("Empty cube light animation file {}.".format(filename))
            continue
        names = sorted(data)
        if len(names) > 1:
            logger.warning("Too many animations in {}: using {}.".format(filename, names[0]))
        name = names[0]
        try:
            patterns = [LightPattern.from_json(entry, "{}{}".format(name, i)) for i, entry in enumerate(data[name])]
        except ValueError as e:
            logger.error("Invalid cube light animation {} in {}. {}".format(name, filename, e))
            continue
        animations[name] = LightAnimation(name, patterns)
    logger.debug("Loaded {} cube light animations in {:.02f} s.".format(
        len(animations), time.perf_counter() - start_time))
    return animations


def load_cube_animation_triggers(resource_dir: str) -> dict[str, str]:
    """ Load the names of the animations of the cube animation triggers. """
    filename = os.path.join(resource_dir, 'cozmo_resources', 'assets', 'cubeAnimationGroupMaps',
                            'CubeAnimationTriggerMap.json')
    data = load_json_file(filename)
    return {str(pair['CladEvent']): str(pair['AnimName']) for pair in data['Pairs']}


@dataclass
class _Playing:
    animation: LightAnimation
    index: int = 0
    deadline: float | None = None
    on_finished: Callable[[], None] | None = None


class CubeLightPlayer:
    """
    Plays the animations of one cube, one layer.

    ``set_lights(states, rotation_frames)`` applies a pattern to the cube (for example with
    ``Client.set_cube_lights``). ``update()`` must be called regularly to advance the patterns.
    """

    def __init__(
            self, set_lights: Callable[[tuple[protocol_encoder.LightState, ...], int], None],
            clock: Callable[[], float] = time.monotonic) -> None:
        self.set_lights = set_lights
        self.clock = clock
        self._stack: list[_Playing] = []

    @property
    def current(self) -> LightAnimation | None:
        return self._stack[-1].animation if self._stack else None

    def play(self, animation: LightAnimation, on_finished: Callable[[], None] | None = None) -> bool:
        """ Play an animation. Returns False when the running animation cannot be overridden. """
        if not animation.patterns:
            return False
        if self._stack and not self._stack[-1].animation.can_be_overridden:
            return False
        if animation.patterns[0].duration_ms == 0:
            self._stack.clear()
        playing = _Playing(animation, on_finished=on_finished)
        self._stack.append(playing)
        self._start_pattern(playing, self.clock())
        return True

    def stop(self) -> None:
        """ Stop the running animation; the previous one, if any, is set again. """
        if not self._stack:
            return
        self._stack.pop()
        if self._stack:
            self._apply(self._stack[-1])

    def stop_all(self) -> None:
        """ Forget all animations. The lights stay as they are. """
        self._stack.clear()

    def update(self) -> None:
        """ Advance the running animation when its pattern is over. """
        if not self._stack:
            return
        playing = self._stack[-1]
        now = self.clock()
        if playing.deadline is None or playing.deadline > now:
            return
        playing.index += 1
        if playing.index >= len(playing.animation.patterns):
            self._stack.pop()
            if playing.on_finished is not None:
                playing.on_finished()
            if self._stack and self._stack[-1] is not playing:
                self._apply(self._stack[-1])
            return
        self._start_pattern(playing, now)

    def _pattern(self, playing: _Playing) -> LightPattern:
        return playing.animation.patterns[playing.index]

    def _apply(self, playing: _Playing) -> None:
        states, rotation = pattern_to_light_states(self._pattern(playing))
        self.set_lights(states, rotation)

    def _start_pattern(self, playing: _Playing, now: float) -> None:
        duration = self._pattern(playing).duration_ms
        playing.deadline = now + duration / 1000.0 if duration else None
        self._apply(playing)
