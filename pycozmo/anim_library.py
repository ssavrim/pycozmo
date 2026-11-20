"""

Library of original animations: clips, animation groups, triggers and selection.

Selection rules (``docs/specs/animation-library.md``):

1. Eligible members have the requested mood, the current head angle within their
   range when they use one, and are not on cooldown.
2. A member is drawn by weight among the eligible ones; its cooldown starts.
3. If none is eligible: another mood falls back to ``Default``; otherwise the
   ``Default`` member whose cooldown ends first is played, with a head angle
   tolerance of 0.05 rad, without starting a new cooldown; otherwise the first member.

Cooldowns are per clip name and shared by all groups of a library.

"""

import math
import os
import pathlib
import random
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from . import anim
from . import anim_encoder
from . import util
from .json_loader import load_json_file
from .logger import logger_animation


__all__ = [
    "DEFAULT_MOOD",
    "HEAD_ANGLE_TOLERANCE_RAD",

    "select_member",
    "AnimationLibrary",
]


DEFAULT_MOOD = "Default"
#: Head angle tolerance of the fallback selection.
HEAD_ANGLE_TOLERANCE_RAD = 0.05

PathLike = Union[str, os.PathLike]


def _in_range(member: anim.AnimationGroupMember, head_angle_rad: Optional[float], tolerance: float = 0.0) -> bool:
    if not member.use_head_angle:
        return True
    if head_angle_rad is None:
        return False
    low = math.radians(member.head_angle_min) - tolerance
    high = math.radians(member.head_angle_max) + tolerance
    return low <= head_angle_rad <= high


def select_member(members: Sequence[anim.AnimationGroupMember], *,
                  cooldowns: Dict[str, float], now: float,
                  head_angle_rad: Optional[float] = None,
                  mood: str = DEFAULT_MOOD,
                  rng: Optional[random.Random] = None,
                  strict: bool = False) -> Optional[anim.AnimationGroupMember]:
    """
    Choose a member of an animation group (docs/specs/animation-library.md).

    :param cooldowns: clip name -> time (same clock as ``now``) at which its cooldown ends. Updated.
    :param now: current time in seconds.
    :param head_angle_rad: current head angle. Without it, members restricted to a head angle range
        are only reachable through the last fallback.
    :param strict: return None instead of falling back when no member is eligible.
    :return: the chosen member, or None if the group has no member of the mood (or strict mode).
    """
    rng_: Any = rng or random
    eligible = [m for m in members
                if m.mood == mood and _in_range(m, head_angle_rad) and cooldowns.get(m.name, -math.inf) <= now]
    if eligible:
        total = sum(m.weight for m in eligible)
        r = rng_.random() * total
        chosen = eligible[-1]   # all weights zero: the last eligible member
        for m in eligible:
            r -= m.weight
            if r < 0:
                chosen = m
                break
        cooldowns[chosen.name] = now + chosen.cooldown_time
        return chosen

    if mood != DEFAULT_MOOD:
        return select_member(members, cooldowns=cooldowns, now=now, head_angle_rad=head_angle_rad,
                             mood=DEFAULT_MOOD, rng=rng, strict=strict)
    if strict or not any(m.mood == mood for m in members):
        return None

    # The member whose cooldown ends first, with some head angle tolerance; no new cooldown.
    best = None
    best_wait = math.inf
    for m in members:
        if m.mood != DEFAULT_MOOD or not _in_range(m, head_angle_rad, HEAD_ANGLE_TOLERANCE_RAD):
            continue
        wait = cooldowns.get(m.name, -math.inf) - now
        if wait < best_wait:
            best, best_wait = m, wait
    if best is not None:
        return best
    return members[0] if members else None


class AnimationLibrary:
    """
    Original animation clips, groups and triggers of prepared resources.

    Clips are loaded lazily. Cooldowns are kept per library.
    """

    def __init__(self, root: PathLike, *, rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic, audio: Any = None) -> None:
        self.root = pathlib.Path(root)
        #: ``anim_audio.AnimAudioRenderer`` used by ``preprocess()``, see ``enable_audio()``.
        self.audio = audio
        self.rng = rng or random.Random()
        self.clock = clock
        assets = self.root / "cozmo_resources" / "assets"
        self.anim_dir = assets / "animations"
        self.face_anim_dir = assets / "faceAnimations"
        self._clip_metadata = anim_encoder.get_clip_metadata(str(self.anim_dir))
        self._clips = {}    # type: Dict[str, anim_encoder.AnimClip]
        self._group_files = {p.stem: p for p in (assets / "animationGroups").rglob("*.json")}
        self._groups = {}   # type: Dict[str, anim.AnimationGroup]
        self._triggers = self._load_pairs(assets / "animationGroupMaps" / "AnimationTriggerMap.json")
        self._cube_triggers = self._load_pairs(assets / "cubeAnimationGroupMaps" / "CubeAnimationTriggerMap.json")
        self._cooldowns = {}    # type: Dict[str, float]

    @classmethod
    def load(cls, root: Optional[PathLike] = None, **kwargs: Any) -> "AnimationLibrary":
        """ Load the library of the resource directory (``$PYCOZMO_DIR/assets`` by default). """
        if root is None:
            util.check_assets()
            root = util.get_cozmo_asset_dir()
        return cls(root, **kwargs)

    @staticmethod
    def _load_pairs(path: pathlib.Path) -> Dict[str, str]:
        if not path.exists():
            return {}
        return {p["CladEvent"]: p["AnimName"] for p in load_json_file(str(path)).get("Pairs", [])}

    # Clips

    def clip_names(self) -> List[str]:
        return sorted(self._clip_metadata)

    def clip(self, name: str) -> anim_encoder.AnimClip:
        """ Load a clip by name. Raises KeyError for an unknown clip. """
        if name not in self._clips:
            fspec = self._clip_metadata[name].fspec
            for clip in anim_encoder.AnimClips.from_fb_file(fspec).clips:
                self._clips[clip.name] = clip
        return self._clips[name]

    def preprocess(self, name: str, **kwargs: Any) -> anim.PreprocessedClip:
        """ PreprocessedClip of a clip, with face animations and, if enabled, sound of this library. """
        kwargs.setdefault("face_anim_dir", self.face_anim_dir)
        kwargs.setdefault("audio", self.audio)
        return anim.PreprocessedClip.from_anim_clip(self.clip(name), **kwargs)

    def audio_dir(self) -> pathlib.Path:
        """ Directory of sound banks and media; the former flat layout is accepted. """
        sound = self.root / "cozmo_resources" / "sound"
        return sound / "AudioAssets" if (sound / "AudioAssets").is_dir() else sound

    def enable_audio(self, **kwargs):
        """
        Play the sound of clips: load the sound banks and create the renderer.

        Keyword arguments go to ``AnimAudioRenderer`` (e.g. ``decoder``). Decoding needs a
        separately installed vgmstream-cli.
        """
        from . import anim_audio
        kwargs.setdefault("rng", self.rng)
        self.audio = anim_audio.AnimAudioRenderer.from_audio_dir(self.audio_dir(), **kwargs)
        return self.audio

    # Groups and triggers

    def group_names(self) -> List[str]:
        return sorted(self._group_files)

    def group(self, name: str) -> anim.AnimationGroup:
        """ Load an animation group by name. Raises KeyError for an unknown group. """
        if name not in self._groups:
            self._groups[name] = anim.AnimationGroup.from_json(load_json_file(str(self._group_files[name])))
        return self._groups[name]

    @property
    def triggers(self) -> Dict[str, str]:
        """ Trigger (CladEvent) -> animation group name. """
        return dict(self._triggers)

    @property
    def cube_triggers(self) -> Dict[str, str]:
        """ Cube trigger -> cube animation group name. """
        return dict(self._cube_triggers)

    def choose(self, trigger: str, *, head_angle_rad: Optional[float] = None, now: Optional[float] = None,
               mood: str = DEFAULT_MOOD, strict: bool = False) -> Optional[str]:
        """
        Choose a clip name for a trigger or a group name, or None.

        :param head_angle_rad: current head angle, used by members restricted to a range.
        :param now: time in seconds of the library clock (``clock()`` by default).
        """
        group_name = self._triggers.get(trigger, trigger)
        try:
            group = self.group(group_name)
        except KeyError:
            logger_animation.warning("Unknown animation trigger or group '%s'.", trigger)
            return None
        member = select_member(group.members, cooldowns=self._cooldowns,
                               now=self.clock() if now is None else now,
                               head_angle_rad=head_angle_rad, mood=mood, rng=self.rng, strict=strict)
        return member.name if member is not None else None

    def reset_cooldowns(self) -> None:
        self._cooldowns.clear()

    def cooldowns(self) -> Dict[str, float]:
        """ Clip name -> end of cooldown, in the library clock. """
        return dict(self._cooldowns)
