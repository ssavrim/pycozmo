"""

Animation clip representation, reading, and preprocessing.

"""

import os
import pathlib
import random
import time
from collections import defaultdict
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple, Union

from PIL import Image
import numpy as np

from . import logger
from . import anim_encoder
from . import image_encoder
from . import lights
from . import procedural_face
from . import protocol_encoder
from . import util
from .json_loader import find_file, load_json_file


__all__ = [
    "BODY_STRAIGHT_RADIUS_MM",
    "BODY_TURN_IN_PLACE_RADIUS_MM",
    "MAX_BODY_SPEED_MMPS",
    "MAX_BODY_TURN_SPEED_DEGPS",

    "PreprocessedClip",
    "AnimationGroupMember",
    "AnimationGroup",

    "load_animation_groups",
    "load_cube_animation_groups",
    "load_backpack_light_patterns"
]


#: AnimBody curvature radius that drives straight.
BODY_STRAIGHT_RADIUS_MM = 0x7fff
#: AnimBody curvature radius that turns in place (speed in deg/s).
BODY_TURN_IN_PLACE_RADIUS_MM = 0
#: Speed limit of straight and arc motions (docs/specs/animation-stream.md).
MAX_BODY_SPEED_MMPS = 220
#: Speed limit of turns in place (docs/specs/animation-stream.md).
MAX_BODY_TURN_SPEED_DEGPS = 300


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _vary(value: int, variability: int, rng: Optional[random.Random]) -> int:
    """Draw from [value - variability, value + variability]."""
    if variability <= 0:
        return value
    return (rng or random).randint(value - variability, value + variability)


#: Face animation frames are played one per animation frame (30 per second).
FACE_ANIMATION_FRAME_MS = 1000.0 / 30


def default_face_anim_dir() -> pathlib.Path:
    """ Face animation directory of the standard resource installation. """
    return util.get_cozmo_asset_dir() / "cozmo_resources" / "assets" / "faceAnimations"


@lru_cache(maxsize=8)
def _face_animation_frames(directory: str, name: str) -> Tuple[Optional[bytes], ...]:
    """
    Encoded DisplayImage payloads of a face animation, None for empty frames.

    Frames are 128x64 grayscale PNG files. Like procedural faces, the robot gets one
    line out of two (128x32); pixels brighter than mid-gray are lit.
    """
    path = pathlib.Path(directory) / name
    frames = []
    for fspec in sorted(path.glob("*.png")):
        with Image.open(fspec) as im:
            pixels = np.array(im.convert("L"))
        lines = pixels[::2] > 127
        if not lines.any():
            frames.append(None)
            continue
        im = Image.fromarray(lines.astype(np.uint8) * 255).convert("1", dither=Image.Dither.NONE)
        frames.append(bytes(image_encoder.ImageEncoder(im).encode()))
    return tuple(frames)


def body_motion_command(radius_mm: Union[float, str], speed: int) -> Tuple[int, int]:
    """
    Convert a BodyMotion keyframe to the (speed, curvature_radius_mm) pair of AnimBody.

    The robot drives arcs itself. Speeds are limited to 220 mm/s straight or on an
    arc and 300 deg/s when turning in place (docs/specs/animation-stream.md).
    """
    if isinstance(radius_mm, str):
        if radius_mm == "STRAIGHT":
            return _clamp(speed, -MAX_BODY_SPEED_MMPS, MAX_BODY_SPEED_MMPS), BODY_STRAIGHT_RADIUS_MM
        if radius_mm in ("TURN_IN_PLACE", "POINT_TURN"):
            return (_clamp(speed, -MAX_BODY_TURN_SPEED_DEGPS, MAX_BODY_TURN_SPEED_DEGPS),
                    BODY_TURN_IN_PLACE_RADIUS_MM)
        radius_mm = float(radius_mm)
    # Values beyond int16 become straight (0x7fff) or the minimum.
    radius = int(radius_mm)
    if radius >= 0x8000:
        radius = BODY_STRAIGHT_RADIUS_MM
    elif radius < -0x8000:
        radius = -0x8000
    return _clamp(speed, -MAX_BODY_SPEED_MMPS, MAX_BODY_SPEED_MMPS), radius


class PreprocessedClip(object):
    """ Preprocessed animation clip that can be played back. """

    def __init__(self, keyframes: Optional[Dict[int, List[protocol_encoder.Packet]]] = None):
        self.keyframes = keyframes or defaultdict(list)

    @classmethod
    def keyframe_to_im(cls, keyframe) -> Image:
        params = [keyframe.center_x, keyframe.center_y, keyframe.scale_x, keyframe.scale_y, keyframe.angle] + \
                 keyframe.left_eye + keyframe.right_eye
        face = procedural_face.ProceduralFace(params)
        im = face.render()
        # The Cozmo protocol expects a 128x32 image, so take only the even lines.
        np_im = np.array(im)
        np_im2 = np_im[::2]
        im = Image.fromarray(np_im2)
        return im

    @classmethod
    def from_anim_clip(cls, clip: anim_encoder.AnimClip, *, allowed_types=None,
                       rng: Optional[random.Random] = None,
                       face_anim_dir: Optional[Union[str, os.PathLike]] = None,
                       audio=None) -> "PreprocessedClip":
        """
        Preprocess all tracks, or only keyframes of the supplied types.

        ``rng`` draws head and lift variability (the module generator by default).
        ``face_anim_dir`` contains face animation frames (``faceAnimations`` of the
        standard resources by default). While a face animation plays, it replaces
        procedural faces.
        ``audio`` is an ``anim_audio.AnimAudioRenderer``; without it, RobotAudio keyframes
        are not played.
        """
        keyframes = defaultdict(list)   # type: Dict[int, List[protocol_encoder.Packet]]
        face_spans = []     # type: List[Tuple[int, int]]
        face_packets = set()
        for keyframe in clip.keyframes:
            if allowed_types is not None and not isinstance(keyframe, allowed_types):
                continue
            if isinstance(keyframe, anim_encoder.AnimHeadAngle):
                angle = _vary(keyframe.angle_deg, keyframe.variability_deg, rng)
                pkt = protocol_encoder.AnimHead(duration_ms=_clamp(keyframe.duration_ms, 0, 0xffff),
                                                angle_deg=_clamp(angle, -128, 127))
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimLiftHeight):
                height = _vary(keyframe.height_mm, keyframe.variability_mm or 0, rng)
                pkt = protocol_encoder.AnimLift(duration_ms=_clamp(keyframe.duration_ms, 0, 0xffff),
                                                height_mm=_clamp(height, 0, 255))
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimRecordHeading):
                pkt = protocol_encoder.RecordHeading()
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimTurnToRecordedHeading):
                pkt = protocol_encoder.TurnToRecordedHeading()
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimBodyMotion):
                # The robot drives straight, in place, or on an arc from (speed, radius).
                speed, radius = body_motion_command(keyframe.radius_mm, keyframe.speed)
                pkt = protocol_encoder.AnimBody(speed=speed, curvature_radius_mm=radius)
                keyframes[keyframe.trigger_time_ms].append(pkt)
                # Stop at the end of the keyframe.
                pkt = protocol_encoder.AnimBody(speed=0, curvature_radius_mm=BODY_STRAIGHT_RADIUS_MM)
                keyframes[keyframe.trigger_time_ms + keyframe.duration_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimBackpackLights):
                left = lights.Color(rgb=(keyframe.left.red, keyframe.left.green, keyframe.left.blue))
                front = lights.Color(rgb=(keyframe.front.red, keyframe.front.green, keyframe.front.blue))
                middle = lights.Color(rgb=(keyframe.middle.red, keyframe.middle.green, keyframe.middle.blue))
                back = lights.Color(rgb=(keyframe.back.red, keyframe.back.green, keyframe.back.blue))
                right = lights.Color(rgb=(keyframe.right.red, keyframe.right.green, keyframe.right.blue))
                pkt = protocol_encoder.AnimBackpackLights(colors=(left.to_int16(),
                                                                  front.to_int16(), middle.to_int16(), back.to_int16(),
                                                                  right.to_int16()))
                keyframes[keyframe.trigger_time_ms].append(pkt)
                off_light = lights.off.to_int16()
                pkt = protocol_encoder.AnimBackpackLights(colors=(off_light,
                                                                  off_light, off_light, off_light,
                                                                  off_light))
                keyframes[keyframe.trigger_time_ms + keyframe.duration_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimFaceAnimation):
                directory = pathlib.Path(face_anim_dir) if face_anim_dir is not None else default_face_anim_dir()
                frames = _face_animation_frames(str(directory), keyframe.anim_name)
                if not frames:
                    logger.warning("Face animation '%s' of clip '%s' not found in %s.",
                                   keyframe.anim_name, clip.name, directory)
                    continue
                start = keyframe.trigger_time_ms
                end = start + round(len(frames) * FACE_ANIMATION_FRAME_MS)
                face_spans.append((start, end))
                for i, payload in enumerate(frames):
                    if payload is None:
                        # Empty frames take their time but send nothing.
                        continue
                    pkt = protocol_encoder.DisplayImage(image=payload)
                    face_packets.add(id(pkt))
                    keyframes[start + round(i * FACE_ANIMATION_FRAME_MS)].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimProceduralFace):
                im = cls.keyframe_to_im(keyframe)
                encoder = image_encoder.ImageEncoder(im)
                buf = bytes(encoder.encode())
                pkt = protocol_encoder.DisplayImage(image=buf)
                keyframes[keyframe.trigger_time_ms].append(pkt)
            elif isinstance(keyframe, anim_encoder.AnimRobotAudio):
                # The whole track is rendered at once below: sounds overlap and are mixed.
                pass
            elif isinstance(keyframe, anim_encoder.AnimEvent):
                # Sent in the stream; the robot reports it back with AnimationEvent when played.
                try:
                    event_type = protocol_encoder.AnimEventType[keyframe.event_id]
                except KeyError:
                    logger.warning("Ignoring unknown animation event '%s' in clip '%s'.",
                                   keyframe.event_id, clip.name)
                    continue
                keyframes[keyframe.trigger_time_ms].append(protocol_encoder.AnimEvent(event_id=event_type))
            else:
                raise RuntimeError("Unexpected keyframe type '{}'".format(type(keyframe)))
        if audio is not None and (allowed_types is None or anim_encoder.AnimRobotAudio in tuple(allowed_types)):
            track = audio.render(clip)
            for line in track.log:
                logger.debug("%s: %s", clip.name, line)
            for at, pkt in track.packets.items():
                keyframes[at].append(pkt)
        # Face animations replace procedural faces while they play.
        if face_spans:
            for at, pkts in list(keyframes.items()):
                if any(start <= at < end for start, end in face_spans):
                    pkts[:] = [p for p in pkts
                               if not isinstance(p, protocol_encoder.DisplayImage) or id(p) in face_packets]
                    if not pkts:
                        del keyframes[at]
        ppclip = cls(keyframes=keyframes)
        return ppclip


class LightAnimation:
    # TODO: create play method for light animations
    __slots__ = [
        "on_colors",
        "off_colors",
        "on_period",
        "off_period",
        "transition_on_period",
        "transition_off_period",
        "offset",
    ]

    def __init__(self,
                 on_colors: List[List],
                 off_colors: List[List],
                 on_period: List[int],
                 off_period: List[int],
                 transition_on_period: List[int],
                 transition_off_period: List[int],
                 offset: List[int]):
        self.on_colors = on_colors
        self.off_colors = off_colors
        self.on_period = on_period
        self.off_period = off_period
        self.transition_on_period = transition_on_period
        self.transition_off_period = transition_off_period
        self.offset = offset


class CubeAnimation(LightAnimation):
    __slots__ = [
        "duration",
        "rotation_period"
    ]

    def __init__(self, duration: int, rotation_period: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.duration = int(duration)
        self.rotation_period = int(rotation_period)

    @classmethod
    def from_json(cls, data: Dict):
        return cls(on_colors=data['pattern']['onColors'],
                   off_colors=data['pattern']['offColors'],
                   on_period=data['pattern']['onPeriod_ms'],
                   off_period=data['pattern']['offPeriod_ms'],
                   transition_on_period=data['pattern']['transitionOnPeriod_ms'],
                   transition_off_period=data['pattern']['transitionOffPeriod_ms'],
                   offset=data['pattern']['offset'],
                   rotation_period=data['pattern']['rotationPeriod_ms'],
                   duration=data['duration_ms'])


class BackpackAnimation(LightAnimation):
    __slots__ = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def from_json(cls, data: Dict):
        return cls(on_colors=data['onColors'],
                   off_colors=data['offColors'],
                   on_period=data['onPeriod_ms'],
                   off_period=data['offPeriod_ms'],
                   transition_on_period=data['transitionOnPeriod_ms'],
                   transition_off_period=data['transitionOffPeriod_ms'],
                   offset=data['offset'])


class AnimationGroupMember:

    __slots__ = [
        "name",
        "weight",
        "cooldown_time",
        "mood",
        "use_head_angle",
        "head_angle_max",
        "head_angle_min",
    ]

    def __init__(self,
                 name: str,
                 weight: float,
                 cooldown_time: float,
                 mood: str,
                 use_head_angle: Optional[bool] = False,
                 head_angle_min: Optional[float] = 0.0,
                 head_angle_max: Optional[float] = 0.0) -> None:
        self.name = str(name)
        self.weight = float(weight)
        self.mood = str(mood)
        self.use_head_angle = bool(use_head_angle)
        # seconds
        self.cooldown_time = float(cooldown_time)
        # Degrees
        self.head_angle_min = float(head_angle_min)
        self.head_angle_max = float(head_angle_max)

    @classmethod
    def from_json(cls, data: Dict):
        return cls(name=data['Name'],
                   weight=data['Weight'],
                   cooldown_time=data['CooldownTime_Sec'],
                   mood=data['Mood'],
                   use_head_angle=data.get('UseHeadAngle', False),
                   head_angle_min=data.get('HeadAngleMin_Deg', 0.0),
                   head_angle_max=data.get('HeadAngleMax_Deg', 0.0))


class AnimationGroup:

    __slots__ = [
        "members",
        "member_probabilities",
    ]

    def __init__(self, members: Iterable[AnimationGroupMember]) -> None:
        self.members = list(members)
        # Normalized weights, for information; selection is done by choose_member().
        weight_sum = sum(m.weight for m in self.members)
        if weight_sum > 0:
            self.member_probabilities = [m.weight / weight_sum for m in self.members]
        else:
            self.member_probabilities = [1.0 / len(self.members)] * len(self.members) if self.members else []

    @classmethod
    def from_json(cls, data: Dict):
        animations = [AnimationGroupMember.from_json(a) for a in data['Animations']]
        return cls(animations)

    def choose_member(self, *, cooldowns: Optional[Dict[str, float]] = None, now: Optional[float] = None,
                      head_angle_rad: Optional[float] = None, mood: str = "Default",
                      rng: Optional[random.Random] = None) -> Optional[AnimationGroupMember]:
        """
        Choose a member (see ``anim_library.select_member`` and docs/specs/animation-library.md).

        Without ``cooldowns``, cooldowns are not remembered between calls.
        """
        from .anim_library import select_member
        return select_member(self.members, cooldowns={} if cooldowns is None else cooldowns,
                             now=time.monotonic() if now is None else now,
                             head_angle_rad=head_angle_rad, mood=mood, rng=rng)


def load_trigger_map(resource_dir: str, map_relative_path: str) -> Tuple[str, str, Dict]:
    json_data = load_json_file(os.path.join(resource_dir, map_relative_path))
    for pair in json_data['Pairs']:
        anim_file = find_file(resource_dir, pair['AnimName'] + '.json')
        if anim_file:
            yield pair['CladEvent'], pair['AnimName'], load_json_file(anim_file)


def load_animation_groups(resource_dir: str) -> Dict[str, AnimationGroup]:
    start_time = time.perf_counter()
    animation_groups = {}
    trigger_map_loader = load_trigger_map(resource_dir, os.path.join('cozmo_resources', 'assets',
                                                                     'animationGroupMaps', 'AnimationTriggerMap.json'))
    for evt, name, json_data in trigger_map_loader:
        animation_groups[evt] = AnimationGroup.from_json(json_data)
    logger.debug("Loaded {} animation groups in {:.02f} s.".format(
        len(animation_groups), time.perf_counter() - start_time))
    return animation_groups


def load_cube_animation_groups(resource_dir: str) -> Dict[str, List[CubeAnimation]]:
    start_time = time.perf_counter()
    cube_animation_groups = {}
    trigger_map_loader = load_trigger_map(resource_dir,
                                          os.path.join('cozmo_resources', 'assets',
                                                       'cubeAnimationGroupMaps', 'CubeAnimationTriggerMap.json'))
    for evt, name, json_data in trigger_map_loader:
        cube_animation_groups[evt] = []
        for cube_anim in json_data[name]:
            cube_animation_groups[evt].append(CubeAnimation.from_json(cube_anim))
    logger.debug("Loaded {} cube animation groups in {:.02f} s.".format(
        len(cube_animation_groups), time.perf_counter() - start_time))
    return cube_animation_groups


def load_backpack_light_patterns(resource_dir: str) -> Dict[str, BackpackAnimation]:
    backpack_light_patterns = {}
    json_data = load_json_file(os.path.join(resource_dir, 'cozmo_resources', 'config',
                               'engine', 'lights', 'backpackLights', 'backpackLightPatterns.json'))

    for key in json_data:
        backpack_light_patterns[key] = BackpackAnimation.from_json(json_data[key])
    return backpack_light_patterns
