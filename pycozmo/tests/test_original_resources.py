"""
Checks on resources prepared from the Cozmo application 3.4.0.

Skipped unless PYCOZMO_TEST_RESOURCES names a resource directory prepared with
``pycozmo_resources.py prepare``. Rendering sound also needs VGMSTREAM_CLI or
vgmstream-cli on the PATH.
"""

import os
import pathlib
import random
import shutil

import pytest

from pycozmo import anim_audio, anim_encoder as ae, anim_library
from pycozmo.audiokinetic import hierarchy as wh


ROOT = os.environ.get("PYCOZMO_TEST_RESOURCES")
pytestmark = pytest.mark.skipif(not ROOT, reason="PYCOZMO_TEST_RESOURCES not set")


@pytest.fixture(scope="module")
def library():
    return anim_library.AnimationLibrary(pathlib.Path(str(ROOT)), rng=random.Random(0))


def test_library_content(library):
    assert len(library.clip_names()) == 993
    assert len(library.group_names()) == 507 and len(library.triggers) == 573
    assert all(library.choose(t, head_angle_rad=0.0, now=0.0) for t in library.triggers)


def test_sound_banks_and_animation_events(library):
    banks = sorted(library.audio_dir().glob("*.bnk")) + sorted(library.audio_dir().glob("*/*.bnk"))
    h = wh.Hierarchy.load(banks)
    assert not h.unreadable
    events = {int(e) & 0xffffffff for name in library.clip_names() for k in library.clip(name).keyframes
              if isinstance(k, ae.AnimRobotAudio) for e in k.audio_event_ids}
    assert len(events) == 380
    assert all(isinstance(h.get(e), wh.Event) for e in events)


@pytest.mark.skipif(not (os.environ.get("VGMSTREAM_CLI") or shutil.which("vgmstream-cli")),
                    reason="vgmstream-cli not available")
def test_render_sample_clips(library):
    renderer = library.enable_audio(decoder=anim_audio.VgmstreamDecoder())
    for name in ("anim_dancing_mambo_01", "anim_dizzy_shake_loop_01", "anim_bored_event_02"):
        track = renderer.render(library.clip(name))
        assert track.packets, name
