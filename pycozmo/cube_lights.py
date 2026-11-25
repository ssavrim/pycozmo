"""

Light patterns of the cubes: reading, conversion to cube packets and playback (docs/specs/cube-light-patterns.md).

"""

import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import lights
from . import protocol_encoder
from .json_loader import get_json_files, load_json_file
from .logger import logger


__all__ = [
    "LightPattern",
    "LightAnimation",
    "CubeLightPlayer",
    "CubeLightManager",

    "load_cube_light_animations",
    "load_cube_animation_triggers",
    "pattern_to_light_states",
]


Color = lights.RGBA


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


def pattern_to_light_states(pattern: LightPattern) -> tuple[tuple[protocol_encoder.LightState, ...], int]:
    """ The four LightState of the pattern and the rotation period, in frames, for the selection of the cube. """
    states = tuple(
        lights.make_light_state(
            pattern.on_colors[i], pattern.off_colors[i], pattern.on_period_ms[i], pattern.off_period_ms[i],
            pattern.transition_on_ms[i], pattern.transition_off_ms[i], pattern.offset[i])
        for i in range(4))
    return states, lights.ms_to_frames(pattern.rotation_period_ms)


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

    @classmethod
    def for_cube(cls, cli: Any, object_id: int, clock: Callable[[], float] = time.monotonic) -> "CubeLightPlayer":
        """ A player that sets the lights of a connected cube (by object ID) with ``Client.set_cube_lights``. """
        def set_lights(states: tuple[protocol_encoder.LightState, ...], rotation: int) -> None:
            cli.set_cube_lights(object_id, states, rotation_period_frames=rotation)
        return cls(set_lights, clock=clock)

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


CUBE_TYPES = frozenset((protocol_encoder.ObjectType.Block_LIGHTCUBE1, protocol_encoder.ObjectType.Block_LIGHTCUBE2,
                        protocol_encoder.ObjectType.Block_LIGHTCUBE3))


class CubeLightManager:
    """
    Plays the light animations on the cubes that are connected.

    A player is created for each cube that connects (and dropped when it disconnects); the animation of the trigger
    ``Connected`` plays on it. ``play`` plays an animation by the name of its trigger (the ``CladEvent`` of
    ``CubeAnimationTriggerMap.json``, for example ``Flash``) or of the animation itself. ``update`` must be called
    regularly (the brain does it once per frame) to advance the patterns.
    """

    CONNECTED = "Connected"

    def __init__(
            self, cli: Any, animations: dict[str, LightAnimation], triggers: dict[str, str],
            clock: Callable[[], float] = time.monotonic) -> None:
        self.cli = cli
        self.animations = animations
        self.triggers = triggers
        self.clock = clock
        self._players: dict[int, CubeLightPlayer] = {}
        self._lock = threading.RLock()

    def attach(self) -> None:
        """ Follow the connection of the cubes of the client. """
        self.cli.add_handler(protocol_encoder.ObjectConnectionState, self.on_connection)

    @property
    def object_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._players)

    def on_connection(self, cli: Any, pkt: Any) -> None:
        del cli
        if pkt.object_type not in CUBE_TYPES and pkt.connected:
            return
        if pkt.connected:
            with self._lock:
                self._players[pkt.object_id] = CubeLightPlayer.for_cube(self.cli, pkt.object_id, clock=self.clock)
            logger.debug("Cube {} connected.".format(pkt.object_id))
            self.play(self.CONNECTED, pkt.object_id)
        else:
            with self._lock:
                self._players.pop(pkt.object_id, None)

    def resolve(self, name: str) -> LightAnimation | None:
        """ The animation of a trigger or of an animation name; None when unknown. """
        animation = self.animations.get(self.triggers.get(name, name))
        if animation is None:
            logger.warning("Unknown cube light animation or trigger {}.".format(name))
        return animation

    def play(self, name: str, object_id: int | None = None, on_finished: Callable[[], None] | None = None) -> int:
        """ Play an animation on a cube, or on every connected cube. Returns the number of cubes it plays on. """
        animation = self.resolve(name)
        if animation is None:
            return 0
        with self._lock:
            players = [self._players[object_id]] if object_id in self._players else (
                list(self._players.values()) if object_id is None else [])
            count = 0
            for player in players:
                try:
                    if player.play(animation, on_finished=on_finished):
                        count += 1
                except Exception as e:
                    logger.error("Failed to play cube light animation {}. {}".format(name, e))
            return count

    def update(self) -> None:
        with self._lock:
            for object_id, player in list(self._players.items()):
                try:
                    player.update()
                except Exception as e:
                    logger.error("Failed to update the lights of cube {}. {}".format(object_id, e))

    def stop(self, off: bool = True) -> None:
        """ Stop the animations and, by default, turn the lights of the cubes off. """
        with self._lock:
            for object_id, player in list(self._players.items()):
                player.stop_all()
                if off:
                    try:
                        self.cli.set_cube_lights(object_id, (lights.off_light,) * 4)
                    except Exception as e:
                        logger.error("Failed to turn off the lights of cube {}. {}".format(object_id, e))
