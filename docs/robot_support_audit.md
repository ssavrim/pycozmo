# Robot support: implementation audit

This document distinguishes code present in the repository from behavior
verified on hardware. A protocol packet or a passing unit test does not by
itself establish that a feature works on every firmware or physical robot.

## Available and partial features

| Feature | Current implementation | Limits and next validation |
| --- | --- | --- |
| Robot sensors and motors | `Client`, protocol packets and `examples/events.py` expose robot state, wheels, head and lift. | Test individual readings and commands on target firmware. Enable cliff protection explicitly; the legacy `Brain` does not do so automatically. |
| Orientation, taps and battery level | `pycozmo.sensors`: orientation from gravity in the body frame (`EvtRobotOrientationChange`, including `UPSIDE_DOWN`), `EvtRobotTapped` from IMU readings while still, `Client.battery_percent`. | Verified on one robot over Wi-Fi: lifted, put on a side, upside down and back upright were recognised; six head and back taps were detected without false detection, and a user saw a reaction to each tap. The back touch value read 0 and the robot's poke report only arrived for collisions while driving. Battery percentage comes from a curve fitted to one robot and is indicative; on the charger it is unavailable. |
| Bluetooth objects and cube LEDs | `available_objects`, `set_object_connection`, `ObjectConnectionState`, and `examples/cube_lights.py`. Cube tap, movement, battery and acceleration packets are defined. | Bluetooth discovery is not visual detection. Cube battery and acceleration need end-to-end tests; some firmware can reuse a connection slot. The separate console has exercised a cube and its lights. |
| Charging-platform LEDs | Protocol packets and `examples/charger_lights.py`. | Only applicable to compatible Bluetooth charging platforms; not verified on our robot. |
| Procedural face | `procedural_face`, expressions and the animation controller render eyes. | These are robot expressions, not recognition of a person's emotion. |
| Speaker and audio streaming | `audio.load_wav`, `audio.u_law_encoding` and `AnimationController.play_audio` send 744-sample 8-bit frames at 30 frames/s. The controller keeps `LEAD_FRAMES` (14) frames queued on the robot, regulated with `AnimationState`. | Verified by listening on one robot over Wi-Fi: smooth with a 14-frame lead, choppy with 3 or 8. Wi-Fi power saving on the computer caused gaps and should be disabled. WAV input is still expected at 22,050 or 48,000 Hz while the robot plays 22,320 samples per second. |
| Original resources | `pycozmo.resources` and `tools/pycozmo_resources.py` prepare resources from the user's application package, record a manifest and verify it. | Tested with synthetic archives and with the 3.4.0 package (all reference counts, no warning); `docs/specs/resources.md`. Other versions are refused unless explicitly allowed. The signer check identifies Anki's certificate; it does not verify signatures. |
| Original animations | FlatBuffer clips, procedural face, head, lift, body, backpack-light and event keyframes are processed in `anim.PreprocessedClip`. Head and lift durations are 16-bit (no longer truncated to 255 ms), body motions are sent as speed and curvature radius, event keyframes are streamed (`AnimEvent`) with `EvtAnimationEvent` dispatched when the robot reports them, and face animation frames are played. `anim_library.AnimationLibrary` selects clips of a trigger or group (cooldowns, head angle, fallbacks; `docs/specs/animation-library.md`). A caller can filter keyframe types. `Client.play_anim_ppclip` places each keyframe on the frame of its trigger time. | `AnimRobotAudio` is processed only with an audio renderer (see Original sounds). The face animation threshold and line field are to be checked on a robot (`docs/specs/animation-library.md`). Message layouts: `docs/specs/animation-stream.md`; all 993 original clips are converted offline. On a robot (firmware 2381, body HW 5): `AnimationEvent` replies (`anim_energy_eat_01`), straight motion and turn in place are verified; arcs go in the right direction (positive radius turns left) over the right distance, but the circle driven has about 2.05 times the commanded radius (measured on a tile with quarter circles and on the floor with 2, 4 and 8 s arcs; the cause is not established, the radius is sent unchanged). Head and lift moves of 3 s are verified (`RobotState.lift_height_rad` is an angle in radians). Playing an unfiltered clip may move the robot. |
| Text-to-speech | `audio.synthesize_speech` and `Client.say_text` use a separately installed eSpeak NG executable. | Synthetic substitute voice, not the original Cozmo voice. WAV conversion and eSpeak output are tested offline; robot playback needs physical validation. |
| Motion detection | Optional `FaceVision` computes a changed-pixel score between consecutive grayscale frames. | The score is not an object tracker and also responds to head, robot, lighting and camera movement. The caller must decide how to use it. |
| Face detection | Optional OpenCV Haar cascade in `FaceVision`, with camera-event example. | A basic frontal detector; tests use controlled images or a stub detector, not an accuracy benchmark. It does not move the head or perform tracking itself. |
| Face recognition | Optional local LBPH model with explicit sample enrollment in `FaceRegistry`. | Basic image matching, not a reliable identity or liveness check. Sample quality, lighting and false positives need real-world evaluation. Face samples stay in the configured local directory. |
| Needs and mood | `pycozmo.needs.NeedsModel` (repair, energy and play levels with decay by band, modifiers, brackets, fullness cooldown, actions and broken parts; `docs/specs/needs.md`) and `pycozmo.emotions` (nine emotions with incremental decay, events with repetition penalty, simple mood; `docs/specs/mood.md`), driven by the configuration of the prepared resources. | Specified and tested offline with synthetic configurations. On a robot (2026-11-21, connected over Wi-Fi, no movement): the needs model ran 125 s on the real clock and matched the same model on a simulated clock (2 decay steps, difference 0); this checks the clock handling, not the decay values. The mood test (`--tests mood`) lifted the robot: its cliff event, fed to `Mood` as `CliffDetected`, lowered Happy, Calm and Brave by 0.12 each, as specified (one event only: the repetition penalty and the decay were not exercised on the robot). Saved state, application pauses, stars and rewards are not modeled. `Brain` applies emotion events through `Mood`; `CompanionBrain` does not use the models yet. |
| Behavior selection | `pycozmo.activity`: `BehaviorChooser` (score with repetition and running penalties, scoring and strict priority choosers), `ActivityStrategy` (`Simple`, `Needs`, `NeedBasedCooldown`; cooldown, minimal and maximal duration, recent on-treads), `Activity.choose` and `StrictPriorityActivity` (priority of sub-activities); `pycozmo.behavior.BehaviorConfig`; `docs/specs/behavior-selection.md`. | Specified and tested offline with synthetic configurations; all 30 activities and 178 behaviors of the 3.4.0 resources load. Nothing starts a behavior: the choice is a pure function of an application-provided `SelectionContext`, which also decides whether each behavior can run. The `Spark`, `Pyramid`, `PlayWithHumans` and `SevereNeedTransition` strategies and the reaction triggers are not modeled. `Brain(cli, freeplay=True)` uses the choice (see Personality and behaviors); `CompanionBrain` does not. On a robot (no movement): with the robot on its charger (2026-11-22), `--tests select` chose `DriveOffCharger` in `Socialize` through `StrictPriorityFreeplay`; off the charger (2026-11-23) it chose `ReactToObstacle`. |
| Reaction triggers | `pycozmo.behavior.ReactionTriggerManager`: the entries of the reaction trigger map (`load_reaction_triggers`) are tried in the order of the triggers; locks, FistBump (armed by an achieved behavior objective, with cooldown, probability and expiration), Frustration (Confident emotion below a threshold, with cooldown), state conditions and latched notifications; `docs/specs/reaction-triggers.md`. | Specified and tested offline with synthetic configurations; the 22 entries of the 3.4.0 map load. The decision starts no behavior and is a pure function of an application-provided `SelectionContext`. The events and timeouts of each trigger, the interruption of a running reaction and the state conditions are left to the application or not modeled. `Brain` uses the manager (see Personality and behaviors). On a robot (2026-11-23): lifting the robot, notified as `RobotPickedUp`, fired `ReactToPickup` once (`--tests react`); with the confidence forced to 0.0, -0.7 and -0.95, `Brain` ran no reaction, `ReactToFrustrationMinor` and `ReactToFrustrationMajor` (`--tests brain`); the started brain ran `ReactToPickup` when the robot was lifted (`--tests brainreact`); FistBump and the state conditions were not run on a robot. |
| Cube light patterns | `pycozmo.cube_lights` and `pycozmo.lights` (`make_light_state`, `encode_color`, `Color.to_int16(cube=True)`): the 37 animations of `config/engine/lights/cubeLights` (`load_cube_light_animations`) and the trigger map are read; a pattern becomes the four `LightState` of the cube and the rotation period (solid LEDs, white balance, 5-bit colors with the alpha as bit 15, frames of 30 ms); `CubeLightPlayer` plays an animation (durations, stack with resume, `canBeOverridden`); `docs/specs/cube-light-patterns.md`. | Specified and tested offline; all 37 animations load and the 40 triggers resolve to one of them. The three layers of the original, the blending, the default animation of an empty layer and the gamma are not modeled; the player does not send anything by itself (`CubeLightPlayer.for_cube` binds it to `Client.set_cube_lights`; `examples/cube_light_animation.py`). On a robot (2026-11-23): five animations played on a cube with `--tests cubelights` (8 patterns sent); the look was confirmed by the operator. |
| Personality and behaviors | `Brain` holds the needs model, the mood, the reaction trigger manager and, when created with `freeplay=True`, chooses the next behavior of the freeplay once per second when nothing runs; a reaction that fires interrupts the running behavior. `CompanionBrain` provides an independent decision policy. | Many original behaviors remain unimplemented, and the brain only chooses behaviors that are implemented (and `DriveOffCharger` only on the charger). Achieved objectives (FistBump), the needs actions and the state conditions are not fed by `Brain` yet. `CompanionBrain` only suggests actions; an application must provide fresh observations, execute actions and enforce motion safety. |

## Candidates for implementation

The ordering reflects value for robot interaction and available evidence, not
a promise of accuracy or effort. Hardware validation requires a connected robot
and a controlled setup.

1. **Safe integration of perception and reactions.** Connect camera and robot
   events to the companion policy, manage observation freshness, and stop or
   inhibit movement for a stale link, camera, cliff, pickup, charger or low
   battery. Unit-test state transitions and timeouts, then validate on hardware.
   The separate `cozmo-rea` console contains a prototype, but its controller is
   application-specific and is not yet a PyCozmo autonomous controller.
2. **Validate and extend text-to-speech.** The optional eSpeak NG interface now
   generates substitute voices and converts them to robot packets. Validate
   playback on hardware and evaluate language/voice behavior. The original
   Cozmo TTS voice is not implemented.
3. **Original animation audio.** Relate animation audio event IDs to extracted
   Wwise sound-bank metadata and WEM assets; implement deterministic selection,
   timing and mixing, with tests for missing events and overlapping sounds.
   `AnimRobotAudio` is parsed but is currently skipped during preprocessing.
4. **Cube interaction APIs.** Expose typed observations for connect/disconnect,
   tap, movement, battery and acceleration on top of the existing packets;
   handle reused object IDs and disconnection races. Light and accelerometer
   stream helpers are available. Validate each packet type with a real cube
   before claiming full support.
5. **Visual cube and platform detection.** Implement and evaluate recognition of
   Cozmo's actual markers; Bluetooth presence alone gives neither camera position
   nor distance. Calibration and perspective/pose estimation need image datasets
   and tests. Generic ArUco support can be a separate feature; it must not be
   presented as recognition of Cozmo's own markers without verification.
6. **Face tracking and recognition quality.** Build an opt-in controller with
   bounded motor commands and stale-image handling around `FaceVision`, then
   benchmark detection and matching under varied conditions. The existing console
   has a prototype; no physical validation of this fork's integration has been
   recorded.
7. **Voice commands.** Integrate an optional PC microphone and a replaceable
   speech-recognition backend, with explicit start/stop, language selection and
   defined offline/network behavior. The separate console currently uses browser
   speech recognition; this is not a PyCozmo microphone API.
8. **Songs and simple sounds.** Generate or load PCM, convert it to Cozmo audio
   packets, and test pitch, timing and volume. This does not require original
   audio assets, but must respect the robot's speaker and packet timing.
9. **Human expressions, pets and other robot detection.** Optional computer-vision
   models are possible, but require suitable models, licensing review, datasets
   and accuracy/latency measurements; they are not present in this repository.
10. **Camera calibration, mapping and drivable area.** Calibration requires
    collected images or trustworthy robot calibration data. Robust mapping,
    localization drift handling and free-space estimation depend on calibrated
    observations and motion safety; wheel odometry and path commands alone are
    not a navigation map.
11. **Vector assets and behaviors.** Investigate their schemas and differences
    independently. Cozmo FlatBuffer support does not imply Vector compatibility.

OTA updates and nonvolatile-memory writes are protocol features, but they are
not prerequisites for implementing the items above and are not part of this audit's
hardware tests.
