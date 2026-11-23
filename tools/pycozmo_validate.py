#!/usr/bin/env python
"""

Validation of animation playback on a Cozmo robot.

Checks what offline tests cannot: how the robot executes the animation stream
messages of docs/specs/animation-stream.md, and, when available, face animations
(docs/specs/animation-library.md) and animation sound (docs/specs/animation-audio.md).

Connect the computer to the robot's Wi-Fi, then:

    pycozmo_validate.py                                  # event, head, lift, body
    pycozmo_validate.py --tests arcs                     # arc investigation only
    pycozmo_validate.py --resources DIR                  # + face animation and a full clip
    pycozmo_validate.py --resources DIR --vgmstream PATH # + sound

DIR is a prepared resource directory (with cozmo_resources/), or a directory with
obb/assets/cozmo_resources/.

Tests (--tests, comma-separated; default: all that apply):
    event   animation event round trip (AnimEvent 0x95 -> AnimationEvent 0xd5)   no movement
    head    head move of 3 s                                                       head only
    lift    lift move of 3 s                                                       lift only
    body    straight, turn in place, arc, stop at the end                          wheels
    arcs    arcs of radius 150 mm for 2, 4 and 8 s, odometry and floor heading     wheels
    face    face animation anim_bored_event_02                                     screen
    sound   sound of a few clips                                                   speaker
    clip    one complete clip                                                      wheels
    needs   needs model driven by the real clock for 2 minutes (docs/specs/needs.md)   no movement
    mood    emotion events from robot events: lift the robot (docs/specs/mood.md)       you lift the robot
    react   reaction trigger from a robot event: lift the robot (docs/specs/reaction-triggers.md)   you lift the robot
    select  behavior selection with the charger state of the robot (docs/specs/behavior-selection.md)   no movement

Tests that move the wheels ask for confirmation first. Ctrl+C stops the motors.
A JSON report is written (--report).

"""

import argparse
import json
import math
import os
import pathlib
import platform
import sys
import threading
from typing import Any
import time

import pycozmo
from pycozmo import anim, anim_encoder as ae, event, protocol_encoder as pe, robot


ALL_TESTS = (
    "event", "head", "lift", "body", "arcs", "face", "sound", "clip", "needs", "mood", "select", "react", "brain",
    "brainreact", "cubelights", "markers", "protocol", "wheels", "cliff", "cliffthreshold", "turn",
    "braincubes", "edges")


class State:
    """ RobotState samples: (host time, x, y, heading rad, head rad, lift mm, left wheel, right wheel). """

    def __init__(self):
        self.lock = threading.Lock()
        self.samples = []

    @staticmethod
    def lift_height_mm(pkt):
        """ Lift height from RobotState, whose ``lift_height_rad`` field is the arm angle in radians
        (about -0.2 at rest, 0.7 fully up on firmware 2381). """
        return robot.LIFT_PIVOT_HEIGHT.mm + robot.LIFT_ARM_LENGTH.mm * math.sin(pkt.lift_height_rad)

    def on_state(self, cli, pkt):
        del cli
        with self.lock:
            self.samples.append((time.monotonic(), pkt.pose_x, pkt.pose_y, pkt.pose_angle_rad, pkt.head_angle_rad,
                                 self.lift_height_mm(pkt), pkt.lwheel_speed_mmps, pkt.rwheel_speed_mmps))
            del self.samples[:-4000]

    def since(self, t0):
        with self.lock:
            return [s for s in self.samples if s[0] >= t0]

    def last(self):
        with self.lock:
            return self.samples[-1] if self.samples else None


def ask(question, default=True):
    try:
        answer = input("{} [{}] ".format(question, "Y/n" if default else "y/N")).strip().lower()
    except EOFError:
        answer = ""
    return default if not answer else answer in ("y", "yes", "o", "oui")


def ask_number(question):
    try:
        answer = input("{} (Enter to skip) ".format(question)).strip().replace(",", ".")
    except EOFError:
        return None
    try:
        return float(answer) if answer else None
    except ValueError:
        return None


def play(cli, clip=None, name="validation", ppclip=None, timeout=15.0):
    if ppclip is None:
        ppclip = anim.PreprocessedClip.from_anim_clip(ae.AnimClip(name, clip))
    done = threading.Event()
    cli.add_handler(event.EvtAnimationCompleted, lambda cli: done.set(), one_shot=True)
    cli.play_anim_ppclip(ppclip)
    done.wait(timeout)


def wrap(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


def time_to_90pct(samples, index, start_value, target, t0):
    span = target - start_value
    if abs(span) < 1e-6:
        return None
    for s in samples:
        if (s[index] - start_value) / span >= 0.9:
            return round(s[0] - t0, 2)
    return None


class Report:
    def __init__(self) -> None:
        self.data: dict[str, Any] = dict(
            date=time.strftime("%Y-%m-%d %H:%M"), python=platform.python_version(),
            pycozmo=getattr(pycozmo, "__version__", "?"), tests=[])

    def add(self, name, status, **details):
        self.data["tests"].append(dict(name=name, status=status, **details))
        extra = ", ".join("{}={}".format(k, v) for k, v in details.items())
        print("  -> {}: {}{}".format(name, status, " ({})".format(extra) if extra else ""))


def wheels_allowed(report, name):
    if ask("Is Cozmo on the floor, off the charger, with 50 cm of free space around it?", False):
        return True
    report.add(name, "SKIP", detail="not confirmed")
    return False


# ---------------------------------------------------------------------------
# Tests

def test_event(cli, state, report, ctx):
    print("\nevent: animation event round trip (no movement)")
    received = []
    handler = cli.add_handler(event.EvtAnimationEvent,
                              lambda cli, event_id, timestamp: received.append((event_id, timestamp)))
    play(cli, [ae.AnimEvent(trigger_time_ms=300, event_id="TAPPED_BLOCK")], "validation_event")
    deadline = time.monotonic() + 3.0
    while not received and time.monotonic() < deadline:
        time.sleep(0.05)
    cli.del_handler(event.EvtAnimationEvent, handler)
    if received:
        event_id, timestamp = received[0]
        report.add("event", "PASS", event=event_id.name, robot_timestamp_ms=timestamp)
    else:
        report.add("event", "FAIL", detail="no AnimationEvent within 3 s")


def test_head(cli, state, report, ctx):
    print("\nhead: head move of 3 s")
    play(cli, [ae.AnimHeadAngle(trigger_time_ms=0, duration_ms=500, angle_deg=-20)], "validation_head_low")
    time.sleep(1.0)
    start = state.last()
    t0 = time.monotonic()
    play(cli, [ae.AnimHeadAngle(trigger_time_ms=0, duration_ms=3000, angle_deg=40)], "validation_head_slow")
    time.sleep(max(0.0, 4.5 - (time.monotonic() - t0)))
    samples = state.since(t0)
    t90 = time_to_90pct(samples, 4, start[4], math.radians(40), t0)
    ok = t90 is not None and 2.0 <= t90 <= 4.0
    report.add("head", "PASS" if ok else "FAIL", seconds_to_90pct=t90, expected="about 2.7",
               final_deg=round(math.degrees(samples[-1][4]), 1) if samples else None)


def test_lift(cli, state, report, ctx):
    print("\nlift: lift move of 3 s")
    play(cli, [ae.AnimLiftHeight(trigger_time_ms=0, duration_ms=500, height_mm=32)], "validation_lift_low")
    time.sleep(1.0)
    start = state.last()
    t0 = time.monotonic()
    play(cli, [ae.AnimLiftHeight(trigger_time_ms=0, duration_ms=3000, height_mm=85)], "validation_lift_slow")
    time.sleep(max(0.0, 4.5 - (time.monotonic() - t0)))
    samples = state.since(t0)
    t90 = time_to_90pct(samples, 5, start[5], 85.0, t0)
    ok = t90 is not None and 2.0 <= t90 <= 4.0
    report.add("lift", "PASS" if ok else "FAIL", seconds_to_90pct=t90, expected="about 2.7",
               final_mm=round(samples[-1][5], 1) if samples else None)
    play(cli, [ae.AnimLiftHeight(trigger_time_ms=0, duration_ms=500, height_mm=32)], "validation_lift_rest")


def body_move(cli, state, radius, speed, duration_ms, name):
    time.sleep(0.5)
    a = state.last()
    t0 = time.monotonic()
    play(cli, [ae.AnimBodyMotion(trigger_time_ms=0, duration_ms=duration_ms, radius_mm=radius, speed=speed)],
         name, timeout=duration_ms / 1000.0 + 10.0)
    time.sleep(max(0.0, duration_ms / 1000.0 + 1.0 - (time.monotonic() - t0)))
    b = state.last()
    tail = state.since(time.monotonic() - 0.4)
    stopped = all(abs(s[6]) < 5 and abs(s[7]) < 5 for s in tail) if tail else None
    return round(math.hypot(b[1] - a[1], b[2] - a[2])), round(math.degrees(wrap(b[3] - a[3]))), stopped


def test_body(cli, state, report, ctx):
    print("\nbody: straight, turn in place, arc")
    if not wheels_allowed(report, "body"):
        return
    d, t, stopped = body_move(cli, state, "STRAIGHT", 50, 2000, "validation_straight")
    report.add("body_straight", "PASS" if 70 <= d <= 130 and abs(t) <= 10 and stopped is not False else "FAIL",
               distance_mm=d, expected_mm=100, heading_deg=t, stopped=stopped)
    d, t, stopped = body_move(cli, state, "TURN_IN_PLACE", 90, 1000, "validation_turn")
    report.add("body_turn_in_place", "PASS" if 65 <= abs(t) <= 115 and d <= 30 and stopped is not False else "FAIL",
               heading_deg=t, expected_deg=90, distance_mm=d, stopped=stopped)
    d, t, stopped = body_move(cli, state, 150.0, 50, 2000, "validation_arc")
    expected = math.degrees(100.0 / 150.0)
    ok = 70 <= d <= 130 and abs(abs(t) - expected) <= 15 and stopped is not False
    report.add("body_arc", "PASS" if ok else "FAIL", heading_deg=t, expected_deg=round(expected), chord_mm=d,
               stopped=stopped)


def test_arcs(cli, state, report, ctx):
    print("\narcs: radius 150 mm at 40 mm/s for 2, 4 and 8 s")
    print("  Mark the robot's start position and heading on the floor before each arc (tape),")
    print("  then measure the heading change with a protractor when it stops.")
    if not wheels_allowed(report, "arcs"):
        return
    for seconds in (2, 4, 8):
        input("  Place Cozmo at the start mark, then press Enter for the {} s arc.".format(seconds))
        d, t, stopped = body_move(cli, state, 150.0, 40, seconds * 1000, "validation_arc_{}s".format(seconds))
        expected = math.degrees(40.0 * seconds / 150.0)
        floor = ask_number("  Heading change measured on the floor, in degrees?")
        report.add("arc_{}s".format(seconds), "MEASURED", odometry_deg=t, floor_deg=floor,
                   expected_deg=round(expected), odometry_ratio=round(t / expected, 2),
                   floor_ratio=round(floor / expected, 2) if floor is not None else None, chord_mm=d, stopped=stopped)


def test_face(cli, state, report, ctx):
    print("\nface: face animation (screen only, about 5 s)")
    ppclip = ctx["lib"].preprocess("anim_bored_event_02", allowed_types=(ae.AnimFaceAnimation,))
    play(cli, ppclip=ppclip, name="anim_bored_event_02")
    time.sleep(1.0)
    ok = ask("Did a clear, smooth animated face play (no missing lines, no frozen image)?")
    report.add("face", "PASS" if ok else "FAIL")


def test_sound(cli, state, report, ctx):
    print("\nsound: animation sound (speaker only)")
    from pycozmo import anim_audio
    lib = ctx["lib"]
    lib.enable_audio(decoder=anim_audio.VgmstreamDecoder(ctx["vgmstream"]))
    cli.set_volume(40000)
    for name, question in (("anim_dizzy_shake_loop_01", "Looping dizzy sound that stops cleanly at the end?"),
                           ("anim_bored_event_02", "Bored sound in sync, without crackles?"),
                           (lib.choose("CodeLabHappy"), "Happy sound at a sensible level, without clipping?")):
        print("   {}".format(name))
        play(cli, ppclip=lib.preprocess(name, allowed_types=(ae.AnimRobotAudio,)), name=name, timeout=40.0)
        time.sleep(1.0)
        report.add("sound:" + name, "PASS" if ask("   " + question) else "FAIL")


def test_clip(cli, state, report, ctx):
    print("\nclip: one complete clip (face, head, lift, wheels, sound)")
    if not wheels_allowed(report, "clip"):
        return
    lib = ctx["lib"]
    name = lib.choose("CodeLabHappy")
    play(cli, ppclip=lib.preprocess(name), name=name, timeout=40.0)
    time.sleep(1.0)
    ok = ask("Did {} look natural (smooth moves, sound in sync)?".format(name))
    report.add("clip:" + name, "PASS" if ok else "FAIL")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_needs(cli, state, report, ctx):
    """ Run the needs model on the real clock while connected and compare it to the same model on a simulated clock. """
    from pycozmo import needs
    print("\nneeds: the needs model on the real clock for 125 s (no movement)")
    config = needs.load_needs_config(str(ctx["root"]))
    model = needs.NeedsModel(config, rng=__import__("random").Random(0), connected=True)
    start = time.monotonic()
    polls = []
    ticks = 0
    last = None
    while time.monotonic() - start < 125.0:
        polls.append(time.monotonic() - start)
        levels = {n: model.level(n) for n in needs.NeedId}
        if last is not None and levels != last:
            ticks += 1
        last = levels
        time.sleep(0.5)
    elapsed = time.monotonic() - start
    # The same model on a simulated clock, queried at the same instants.
    clock = FakeClock()
    reference = needs.NeedsModel(config, clock=clock, rng=__import__("random").Random(0), connected=True)
    for t in polls:
        clock.now = t
        reference.update()
    expected = {n: reference.levels[n] for n in needs.NeedId}
    measured = {n: model.levels[n] for n in needs.NeedId}
    worst = max(abs(measured[n] - expected[n]) for n in needs.NeedId)
    rounded = {n.name: round(v, 4) for n, v in measured.items()}
    status = "PASS" if ticks == int(elapsed // config.decay_period_seconds) and worst < 1e-3 else "FAIL"
    report.add("needs", status, elapsed_s=round(elapsed, 1), decay_steps=ticks, max_difference=round(worst, 6),
               levels=rounded)


def test_select(cli, state, report, ctx):
    """ Choose a behavior of the freeplay with DriveOffCharger runnable only when the robot is on its charger. """
    from pycozmo import activity
    print("\nselect: behavior selection with the charger state of the robot (no movement)")
    activities = activity.load_activities(str(ctx["root"]))
    on_charger = bool(cli.robot_status & pycozmo.robot.RobotStatusFlag.IS_ON_CHARGER)
    context = activity.SelectionContext(
        runnable=lambda behavior_id: behavior_id != "DriveOffCharger" or on_charger)
    top = activities["StrictPriorityFreeplay"]
    behavior_id = top.choose(context, activities)
    choice = (getattr(top, "current", None), behavior_id)
    chosen_drive_off = behavior_id == "DriveOffCharger"
    report.add("select", "PASS" if chosen_drive_off == on_charger and behavior_id is not None else "FAIL",
               on_charger=on_charger, choice=choice)


def test_react(cli, state, report, ctx):
    """ Notify the reaction trigger manager of the pick up of the robot and check the reaction that fires. """
    from pycozmo import behavior
    print("\nreact: reaction trigger from a robot event")
    manager = behavior.ReactionTriggerManager(behavior.load_reaction_triggers(str(ctx["root"])))
    seen = []

    def on_picked_up(cli, picked_up):
        if picked_up:
            manager.notify("RobotPickedUp")
            seen.append([e.behavior_id for e in manager.check()])

    cli.add_handler(event.EvtRobotPickedUpChange, on_picked_up)
    print("Lift the robot (and put it down) within 20 s.")
    deadline = time.monotonic() + 20.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.1)
    cli.del_handler(event.EvtRobotPickedUpChange, on_picked_up)
    if not seen:
        report.add("react", "FAIL", detail="no pick up within 20 s")
        return
    report.add("react", "PASS" if seen[0] == ["ReactToPickup"] else "FAIL", fired=seen[0])
    if manager.check():
        report.add("react:used", "FAIL", detail="the notification was not used")


def test_cubelights(cli, state, report, ctx):
    """ Play animations of the cube lights on a cube; the packets are checked here, the look by the operator. """
    from pycozmo import cube_lights
    print("\ncubelights: animations on a cube (wake the cube by moving or tapping it; no movement of the robot)")
    animations = cube_lights.load_cube_light_animations(str(ctx["root"]))
    cube_types = {pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE1,
                  pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE2,
                  pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE3}
    deadline = time.monotonic() + 30.0
    factory_id = None
    while factory_id is None and time.monotonic() < deadline:
        factory_id = next((i for i, o in list(cli.available_objects.items()) if o.object_type in cube_types), None)
        time.sleep(0.1)
    if factory_id is None:
        report.add("cubelights", "FAIL", detail="no cube found within 30 s")
        return
    connected = threading.Event()
    found = {}

    def on_connection(_cli, pkt):
        if pkt.factory_id == factory_id and pkt.connected:
            found["object_id"] = pkt.object_id
            connected.set()

    handler = cli.add_handler(pycozmo.protocol_encoder.ObjectConnectionState, on_connection)
    try:
        cli.set_object_connection(factory_id)
        if not connected.wait(10.0):
            report.add("cubelights", "FAIL", detail="the cube did not connect")
            return
        object_id = found["object_id"]
        sent = []

        def set_lights(states, rotation):
            sent.append(len(states))
            cli.set_cube_lights(object_id, states, rotation_period_frames=rotation)

        player = cube_lights.CubeLightPlayer(set_lights)
        order = ("flash", "speedTapWin", "dancing_01", "pyramidFlourish", "wakeUp")
        for name in order:
            print("   playing {}".format(name))
            finished = threading.Event()
            player.play(animations[name], on_finished=finished.set)
            end = time.monotonic() + 8.0
            while not finished.is_set() and time.monotonic() < end:
                player.update()
                time.sleep(0.02)
            time.sleep(1.5)
        player.stop_all()
        cli.set_cube_lights(object_id, (pycozmo.lights.off_light,) * 4)
    finally:
        cli.del_handler(pycozmo.protocol_encoder.ObjectConnectionState, handler)
    report.add("cubelights", "PASS" if len(sent) >= len(order) else "FAIL", packets=len(sent), order=",".join(order),
               look="to confirm by the operator")


def _make_brain(cli, ctx, **kwargs):
    """ Create a brain on the prepared resources. """
    import tempfile
    from pycozmo import brain as brain_module
    with tempfile.TemporaryDirectory() as tmp:
        os.symlink(os.path.abspath(str(ctx["root"])), os.path.join(tmp, "assets"))
        previous = os.environ.get("PYCOZMO_DIR")
        os.environ["PYCOZMO_DIR"] = tmp
        try:
            return brain_module.Brain(cli, **kwargs)
        finally:
            if previous is None:
                del os.environ["PYCOZMO_DIR"]
            else:
                os.environ["PYCOZMO_DIR"] = previous


def test_brainreact(cli, state, report, ctx):
    """ Start the brain and check that lifting the robot makes it run the pick up reaction. """
    print("\nbrainreact: the started brain reacts to the pick up of the robot")
    brain = _make_brain(cli, ctx)
    seen = []
    original = brain.activate_behavior

    def spy(behavior_id, *args, **kwargs):
        seen.append(behavior_id)
        original(behavior_id, *args, **kwargs)

    brain.activate_behavior = spy
    brain.start()
    try:
        print("Lift the robot (and put it down) within 20 s.")
        deadline = time.monotonic() + 20.0
        while "ReactToPickup" not in seen and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        brain.stop()
    report.add("brainreact", "PASS" if "ReactToPickup" in seen else "FAIL", behaviors=seen)


def test_markers(cli, state, report, ctx):
    """ Recognize the markers of the cubes and of the charger in the images of the robot camera. """
    import collections
    import numpy as np
    from pycozmo import markers
    print("\nmarkers: recognition of the markers in the camera images (put a cube in front of the robot)")
    try:
        library = markers.load_marker_library(str(ctx["root"]))
    except FileNotFoundError:
        report.add("markers", "SKIP", detail="no marker library in the resources: prepare them from the APK")
        return
    detector = markers.MarkerDetector(library)
    frames: list[tuple[float, Any]] = []

    def on_image(_cli, image):
        if len(frames) < 10 and (not frames or time.monotonic() - frames[-1][0] > 0.3):
            frames.append((time.monotonic(), np.asarray(image.convert("L"))))

    cli.set_head_angle(0.0)
    time.sleep(1.0)
    cli.add_handler(event.EvtNewRawCameraImage, on_image)
    cli.enable_camera(True, color=False)
    deadline = time.monotonic() + 15.0
    while len(frames) < 10 and time.monotonic() < deadline:
        time.sleep(0.1)
    cli.del_handler(event.EvtNewRawCameraImage, on_image)
    cli.enable_camera(False)
    if not frames:
        report.add("markers", "FAIL", detail="no image")
        return
    counts: collections.Counter = collections.Counter()
    with_marker = 0
    started = time.monotonic()
    for _, gray in frames:
        found = detector.detect(gray)
        with_marker += bool(found)
        counts.update(m.name for m in found)
    per_frame = (time.monotonic() - started) / len(frames)
    for name, count in counts.most_common():
        print("   {} in {} of {} images".format(name, count, len(frames)))
    report.add("markers", "PASS" if with_marker * 2 >= len(frames) else "FAIL", images=len(frames),
               with_marker=with_marker, seconds_per_image=round(per_frame, 2), markers=dict(counts))


def test_protocol(cli, state, report, ctx):
    """ Listen for the messages of the original application that PyCozmo now declares, and ask for IMU data. """
    import collections
    from pycozmo import protocol_base
    from pycozmo import protocol_encoder as pe
    print("\nprotocol: messages of the original application (lift the robot and put it back within 20 s)")
    names = ("IMURawDataChunk", "CliffEvent", "PotentialCliff", "MotorAutoEnabled", "RobotStopped", "RobotErrorReport",
             "IMUTemperature",
             "MainCycleTimeError", "FactoryTestParameter", "WiFiFlashID", "RampTraverseStart", "RampTraverseComplete",
             "BridgeTraverseStart", "BridgeTraverseComplete", "PrintText", "GoalPose", "DataDump", "TimeProfileStat",
             "IMUDataChunk", "DefaultCameraParams", "CrashReport", "FWVersionInfo")
    seen: collections.Counter = collections.Counter()
    examples: dict[str, str] = {}
    unknown: collections.Counter = collections.Counter()
    unknown_sizes: dict[int, set] = collections.defaultdict(set)

    def make(name):
        def handler(_cli, pkt):
            seen[name] += 1
            examples.setdefault(name, repr(pkt))
        return handler

    def on_unknown(_cli, pkt):
        unknown[pkt.id] += 1
        unknown_sizes[pkt.id].add(len(pkt.data))

    handlers = [(getattr(pe, n), make(n)) for n in names]
    handlers += [(protocol_base.UnknownCommand, on_unknown), (protocol_base.UnknownEvent, on_unknown)]
    for cls, handler in handlers:
        cli.add_handler(cls, handler)
    try:
        cli.conn.send(pe.IMURequest(length_ms=500))
        time.sleep(1.5)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        for cls, handler in handlers:
            cli.del_handler(cls, handler)
    for name, count in sorted(seen.items()):
        print("   {} x{}: {}".format(name, count, examples[name]))
    for number, count in sorted(unknown.items()):
        print("   not declared 0x{:02x} x{} sizes {}".format(number, count, sorted(unknown_sizes[number])))
    # IMURequest must give a stream of IMURawDataChunk: 500 ms at about 180 per second.
    report.add("protocol", "PASS" if seen["IMURawDataChunk"] >= 50 else "FAIL", declared_seen=dict(seen),
               not_declared={hex(k): v for k, v in unknown.items()})


def test_wheels(cli, state, report, ctx):
    """ TurnInPlaceAtAngularSpeed (0x3a): turn on the spot at a given angular speed, 3 s at 0.5 rad/s. """
    from pycozmo import protocol_encoder as pe
    print("\nwheels: TurnInPlaceAtAngularSpeed, 0.5 rad/s for 3 s (put Cozmo on the floor with 30 cm free around it)")
    if not wheels_allowed(report, "wheels"):
        return
    input("  Press Enter to start.")
    t0 = time.monotonic()
    first = state.last()
    try:
        while time.monotonic() - t0 < 3.0:
            cli.conn.send(pe.TurnInPlaceAtAngularSpeed(speed_rad_per_sec=0.5, accel_rad_per_sec2=0.0))
            time.sleep(0.1)
    finally:
        cli.stop_all_motors()
    time.sleep(1.0)
    last = state.last()
    turned = None
    if first and last:
        turned = math.degrees(last[3] - first[3])
    peak = max((abs(s[6]) + abs(s[7]) for s in state.since(t0)), default=0.0)
    print("   heading change {} deg, peak wheel speed sum {:.0f} mm/s".format(
        None if turned is None else round(turned), peak))
    floor = ask_number("  Heading change measured on the floor, in degrees?")
    report.add("wheels", "MEASURED", odometry_deg=None if turned is None else round(turned), floor_deg=floor,
               expected_deg=round(math.degrees(0.5 * 3.0)), peak_wheel_sum_mmps=round(peak))


def test_cliff(cli, state, report, ctx):
    """ Messages sent when the floor disappears under a driving robot: drive slowly while it is held in the air. """
    import collections
    from pycozmo import protocol_encoder as pe
    print("\ncliff: Cozmo drives at 20 mm/s for 15 s. HOLD IT in your hand, wheels free, then lift it from the floor")
    print("  and put it back (or hold it over a table edge). Keep your hand under it.")
    if not ask("Ready, holding the robot above the floor?", False):
        report.add("cliff", "SKIP", detail="not confirmed")
        return
    seen: collections.Counter = collections.Counter()
    examples: dict[str, str] = {}
    names = ("CliffEvent", "PotentialCliff", "RobotStopped", "MotorAutoEnabled", "RobotErrorReport")

    def make(name):
        def handler(_cli, pkt):
            seen[name] += 1
            examples.setdefault(name, repr(pkt))
        return handler

    handlers = [(getattr(pe, n), make(n)) for n in names]
    for cls, handler in handlers:
        cli.add_handler(cls, handler)
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15.0:
            cli.drive_wheels(20.0, 20.0)
            time.sleep(0.1)
    finally:
        cli.stop_all_motors()
        for cls, handler in handlers:
            cli.del_handler(cls, handler)
    for name, count in sorted(seen.items()):
        print("   {} x{}: {}".format(name, count, examples[name]))
    report.add("cliff", "MEASURED", seen=dict(seen))


def test_cliffthreshold(cli, state, report, ctx):
    """ SetCliffDetectThreshold (0x54): change the threshold and watch the cliff flag and the raw cliff values. """
    from pycozmo import protocol_encoder as pe, robot as rb
    print("\ncliffthreshold: robot at rest on the floor, not moving. The threshold stays set until the robot is")
    print("  restarted (take it off the charger or switch it off and on afterwards).")
    if not ask("Is Cozmo resting on the floor, on a light surface, not on the charger?", False):
        report.add("cliffthreshold", "SKIP", detail="not confirmed")
        return
    latest: dict = {}

    def on_state(_cli, pkt):
        latest["flag"] = bool(pkt.status & rb.RobotStatusFlag.CLIFF_DETECTED)
        latest["raw"] = list(pkt.cliff_data_raw)

    cli.add_handler(pe.RobotState, on_state)
    results: dict = {}
    try:
        time.sleep(1.0)
        print("   at start: {}".format(latest))
        results["start"] = dict(latest)
        for threshold in (0, 100, 300, 600, 1000, 1500, 2500, 4000, 0):
            cli.conn.send(pe.SetCliffDetectThreshold(threshold=threshold))
            time.sleep(1.5)
            print("   threshold {:5d}: {}".format(threshold, latest))
            results[threshold] = dict(latest)
    finally:
        cli.del_handler(pe.RobotState, on_state)
    report.add("cliffthreshold", "MEASURED", results={str(k): v for k, v in results.items()})


def test_turn(cli, state, report, ctx):
    """ TurnInPlace (0x39): relative turn of 90 degrees, then with one half revolution more. """
    from pycozmo import protocol_encoder as pe
    print("\nturn: TurnInPlace 90 degrees, with num_half_revolutions 0 then 1 (30 cm free around Cozmo)")
    if not wheels_allowed(report, "turn"):
        return
    results = {}
    for halves in (0, 1):
        input("  Press Enter for the turn with num_half_revolutions={}.".format(halves))
        first = state.last()
        cli.conn.send(pe.TurnInPlace(angle_rad=math.pi / 2, speed_rad_per_sec=1.0, accel_rad_per_sec2=3.0,
                                     angle_tolerance_rad=0.05, num_half_revolutions=halves, is_absolute=False,
                                     action_id=1))
        time.sleep(6.0)
        cli.stop_all_motors()
        time.sleep(0.5)
        last = state.last()
        turned = math.degrees(last[3] - first[3]) if first and last else None
        floor = ask_number("  Turn seen on the floor, in degrees?")
        print("   odometry {} deg".format(None if turned is None else round(turned)))
        results[halves] = dict(odometry_deg=None if turned is None else round(turned), floor_deg=floor)
    report.add("turn", "MEASURED", results={str(k): v for k, v in results.items()})


def test_braincubes(cli, state, report, ctx):
    """ The brain plays the light animations on a cube that connects, reacts to a cube that moves and sees. """
    print("\nbraincubes: Brain(markers=True) with a cube (the packets are checked here, the lights by the operator)")
    brain = _make_brain(cli, ctx, markers=True)
    seen = []
    original = brain.activate_behavior

    def spy(behavior_id, *args, **kwargs):
        seen.append(behavior_id)
        original(behavior_id, *args, **kwargs)

    brain.activate_behavior = spy
    cube_types = {pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE1,
                  pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE2,
                  pycozmo.protocol_encoder.ObjectType.Block_LIGHTCUBE3}
    brain.start()
    markers_seen: set[str] = set()
    try:
        print("  Wake a cube (move or tap it); it connects by itself.")
        deadline = time.monotonic() + 30.0
        factory_id = None
        while factory_id is None and time.monotonic() < deadline:
            factory_id = next((i for i, o in list(cli.available_objects.items()) if o.object_type in cube_types), None)
            time.sleep(0.1)
        if factory_id is None:
            report.add("braincubes", "FAIL", detail="no cube found within 30 s")
            return
        cli.set_object_connection(factory_id)
        deadline = time.monotonic() + 10.0
        while not brain.cube_lights.object_ids and time.monotonic() < deadline:
            time.sleep(0.1)
        if not brain.cube_lights.object_ids:
            report.add("braincubes", "FAIL", detail="the cube did not connect")
            return
        print("  Cube connected: the 'Connected' animation plays (watch the cube).")
        time.sleep(4.0)
        for name in ("Flash", "Dance_01"):
            print("  play_cube_animation({!r}): {} cube(s)".format(name, brain.play_cube_animation(name)))
            time.sleep(4.0)
        print("  Now MOVE the cube, then hold it 15 cm in front of the camera, for 25 s.")
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            markers_seen.update(m.name for m in list(brain.visible_markers))
            time.sleep(0.2)
    finally:
        brain.stop()
    watcher = brain.marker_watcher
    images = (watcher.submitted, watcher.processed) if watcher is not None else (None, None)
    print("   images submitted/processed: {}/{}; markers seen: {}; behaviors run: {}".format(
        images[0], images[1], sorted(markers_seen), seen))
    report.add("braincubes", "MEASURED", cubes=len(brain.cube_lights.object_ids), markers=sorted(markers_seen),
               images_submitted=images[0], images_processed=images[1], behaviors=seen,
               look="the cube animations are to confirm by the operator")


def test_edges(cli, state, report, ctx):
    """ The table edge: drive slowly at it, note the drop, then retreat with CliffRetreat (a hand under the robot). """
    from pycozmo import edges
    print("\nedges: the robot drives at 15 mm/s toward the edge of the table, stops at the drop and backs away")
    print("  Put Cozmo on a table, about 15 cm from the edge, facing it, with the edge clear in front.")
    print("  Keep a hand ready under the robot. The test stops at the first drop and moves only a few centimeters.")
    if not ask("Is Cozmo on the table facing the edge, 15 cm away, with a hand ready?", False):
        report.add("edges", "SKIP", detail="not confirmed")
        return
    memory = edges.TableMemory()

    def on_state(_cli, *_):
        pose = cli.pose
        memory.observe(pose.position.x, pose.position.y, pose.rotation.angle_z.radians, pose.origin_id,
                       cli.robot_status, time.monotonic())

    handler = cli.add_handler(event.EvtRobotStateUpdated, on_state)
    cliff = int(robot.RobotStatusFlag.CLIFF_DETECTED)
    outcome = None
    try:
        time.sleep(1.0)
        start = cli.pose
        deadline = time.monotonic() + 12.0
        cli.drive_wheels(15.0, 15.0)
        while time.monotonic() < deadline and not cli.robot_status & cliff:
            time.sleep(0.02)
        cli.stop_all_motors()
        saw = bool(cli.robot_status & cliff)
        pose = cli.pose
        travelled = math.hypot(pose.position.x - start.position.x, pose.position.y - start.position.y)
        time.sleep(0.5)
        heading = pose.rotation.angle_z.radians
        origin = pose.origin_id
        now = time.monotonic()
        print("   drop seen: {}, travelled {:.0f} mm, edges noted: {}, poses recorded: {}".format(
            saw, travelled, len(memory.edges_known(origin, now)), len(memory.poses)))
        print("   backup room {:.0f} mm, can turn here: {}".format(
            memory.backup_room(pose.position.x, pose.position.y, heading, origin),
            memory.can_turn(pose.position.x, pose.position.y, origin, now)))
        if saw:
            retreat = edges.CliffRetreat(pose.position.x, pose.position.y, heading, time.monotonic(),
                                         memory.backup_room(pose.position.x, pose.position.y, heading, origin,
                                                            edges.CliffRetreat.BACKUP_MM))
            commands = retreat.first()
            end = time.monotonic() + 15.0
            while commands is not None and time.monotonic() < end:
                for command in commands:
                    if command[0] == "wheels":
                        cli.drive_wheels(command[1], command[2])
                    elif command[0] == "stop":
                        cli.stop_all_motors()
                    elif command[0] == "done":
                        outcome = command[1]
                if outcome is not None:
                    break
                time.sleep(0.05)
                pose = cli.pose
                commands = retreat.step(
                    pose.position.x, pose.position.y, pose.rotation.angle_z.radians,
                    bool(cli.robot_status & cliff), time.monotonic(),
                    memory.can_turn(pose.position.x, pose.position.y, pose.origin_id, time.monotonic()))
            print("   retreat outcome: {}".format(outcome))
    finally:
        cli.stop_all_motors()
        cli.del_handler(event.EvtRobotStateUpdated, handler)
    # Whether the robot stayed on the table is seen by the operator, not by this tool.
    report.add("edges", "MEASURED", drop_seen=saw, travelled_mm=round(travelled), retreat=outcome,
               edges_noted=len(memory.edges), look="the operator confirms that the robot stayed on the table")


def test_brain(cli, state, report, ctx):
    """ Make the brain react to a frustration with two levels of confidence and check the reaction it runs. """
    print("\nbrain: frustration reactions chosen from the confidence")
    brain = _make_brain(cli, ctx)
    got = {}
    for confidence in (0.0, -0.7, -0.95):
        brain.deactivate_behavior()
        brain.mood.emotion_types["Confident"].set(confidence)
        brain.check_reactions()
        got[confidence] = brain.behavior.get_id() if brain.behavior else None
        time.sleep(3.0)
    brain.deactivate_behavior()
    expected = {0.0: None, -0.7: "ReactToFrustrationMinor", -0.95: "ReactToFrustrationMajor"}
    report.add("brain", "PASS" if got == expected else "FAIL", reactions={str(k): v for k, v in got.items()})
    # Freeplay: with nothing running and no frustration, the brain chooses an implemented behavior by itself.
    brain.emotion_types["Confident"].set(0.0)
    brain.freeplay = True
    chosen = brain.choose_behavior()
    time.sleep(3.0)
    brain.deactivate_behavior()
    report.add("brain:freeplay", "PASS" if chosen is not None else "FAIL", chosen=chosen)


def test_mood(cli, state, report, ctx):
    """ Feed the mood with the cliff event of the robot and check the emotion values and the penalty. """
    from pycozmo import emotions
    print("\nmood: emotion events from robot events")
    root = str(ctx["root"])
    types = emotions.load_emotion_types(root)
    mood = emotions.Mood(types, emotions.load_emotion_events(root))
    seen = []

    def on_cliff(cli, detected):
        if detected:
            seen.append((time.monotonic(), mood.apply_event("CliffDetected"), mood.values()))

    cli.add_handler(event.EvtCliffDetectedChange, on_cliff)
    before = mood.values()
    print("Lift the robot (and put it down) within 20 s; lifting it is seen as a cliff.")
    deadline = time.monotonic() + 20.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.1)
    cli.del_handler(event.EvtCliffDetectedChange, on_cliff)
    if not seen:
        report.add("mood", "FAIL", detail="no cliff event within 20 s")
        return
    after = seen[0][2]
    changes = {k: round(after[k] - before[k], 3) for k in after if abs(after[k] - before[k]) > 1e-6}
    expected = {"Happy": -0.12, "Calm": -0.12, "Brave": -0.12}
    ok = all(abs(changes.get(k, 0) - v) < 0.01 for k, v in expected.items()) and mood.simple_mood() in (
        "Default", "Sad")
    report.add("mood", "PASS" if ok else "FAIL", first_event_changes=changes, events_seen=len(seen),
               simple_mood=mood.simple_mood())


# ---------------------------------------------------------------------------

def resource_root(path):
    p = pathlib.Path(path).expanduser()
    for candidate in (p, p / "obb" / "assets"):
        if (candidate / "cozmo_resources" / "assets" / "animations").is_dir():
            return candidate
    sys.exit("No cozmo_resources/assets/animations under {} (or obb/assets/).".format(p))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tests", default=",".join(ALL_TESTS), help="comma-separated tests (default: all)")
    parser.add_argument("--resources", help="resource directory, for face, sound and clip")
    parser.add_argument("--vgmstream", help="vgmstream-cli executable, for sound")
    parser.add_argument("--no-motion", action="store_true", help="skip tests that move the wheels")
    parser.add_argument("--report", default="robot-validation-report.json")
    args = parser.parse_args()
    args.tests = [t.strip() for t in args.tests.split(",") if t.strip()]
    unknown = set(args.tests) - set(ALL_TESTS)
    if unknown:
        parser.error("unknown tests: {}".format(", ".join(sorted(unknown))))
    return args


def main():
    args = parse_args()
    report = Report()
    ctx = dict(lib=None, vgmstream=args.vgmstream, root=None)
    if args.resources:
        ctx["root"] = resource_root(args.resources)
        try:
            from pycozmo import anim_library
        except ImportError:
            print("This pycozmo has no anim_library: face, sound and clip tests are skipped.")
        else:
            ctx["lib"] = anim_library.AnimationLibrary(resource_root(args.resources))

    tests = []
    for name in args.tests:
        if args.no_motion and name in ("body", "arcs", "clip"):
            report.add(name, "SKIP", detail="--no-motion")
        elif name in ("face", "sound", "clip") and ctx["lib"] is None:
            report.add(name, "SKIP", detail="needs --resources and pycozmo.anim_library")
        elif name in ("needs", "mood", "select", "react", "brain", "brainreact", "cubelights", "markers") and (
                ctx["root"] is None):
            report.add(name, "SKIP", detail="needs --resources")
        elif name == "sound" and not args.vgmstream:
            report.add(name, "SKIP", detail="needs --vgmstream")
        else:
            tests.append(globals()["test_" + name])

    state = State()
    print("Connecting to the robot (the computer must be on Cozmo's Wi-Fi)...")
    with pycozmo.connect(enable_procedural_face=False, log_level="WARNING") as cli:
        cli.add_handler(pe.RobotState, state.on_state)
        time.sleep(1.0)
        if state.last() is None:
            sys.exit("No robot state received.")
        try:
            for test in tests:
                test(cli, state, report, ctx)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            report.data["interrupted"] = True
        finally:
            cli.stop_all_motors()

    pathlib.Path(args.report).write_text(json.dumps(report.data, indent=2) + "\n", encoding="utf-8")
    counts = {s: sum(t["status"] == s for t in report.data["tests"]) for s in ("PASS", "FAIL", "MEASURED", "SKIP")}
    print("\n{PASS} passed, {FAIL} failed, {MEASURED} measured, {SKIP} skipped. Report: {0}".format(
        args.report, **counts))


if __name__ == "__main__":
    main()
