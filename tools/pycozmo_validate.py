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

Tests that move the wheels ask for confirmation first. Ctrl+C stops the motors.
A JSON report is written (--report).

"""

import argparse
import json
import math
import pathlib
import platform
import sys
import threading
from typing import Any
import time

import pycozmo
from pycozmo import anim, anim_encoder as ae, event, protocol_encoder as pe, robot


ALL_TESTS = ("event", "head", "lift", "body", "arcs", "face", "sound", "clip", "needs", "mood")


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
        elif name in ("needs", "mood") and ctx["root"] is None:
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
