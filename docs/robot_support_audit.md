# Robot support: implementation audit

This document distinguishes code present in the repository from behavior
verified on hardware. A protocol packet or a passing unit test does not by
itself establish that a feature works on every firmware or physical robot.

## Available and partial features

| Feature | Current implementation | Limits and next validation |
| --- | --- | --- |
| Robot sensors and motors | `Client`, protocol packets and `examples/events.py` expose robot state, wheels, head and lift. | Test individual readings and commands on target firmware. Enable cliff protection explicitly; the legacy `Brain` does not do so automatically. |
| Bluetooth objects and cube LEDs | `available_objects`, `set_object_connection`, `ObjectConnectionState`, and `examples/cube_lights.py`. Cube tap, movement, battery and acceleration packets are defined. | Bluetooth discovery is not visual detection. Cube battery and acceleration need end-to-end tests; some firmware can reuse a connection slot. The separate console has exercised a cube and its lights. |
| Charging-platform LEDs | Protocol packets and `examples/charger_lights.py`. | Only applicable to compatible Bluetooth charging platforms; not verified on our robot. |
| Procedural face | `procedural_face`, expressions and the animation controller render eyes. | These are robot expressions, not recognition of a person's emotion. |
| Speaker and audio streaming | `audio.load_wav`, `audio.u_law_encoding` and `AnimationController.play_audio` send 744-sample 8-bit frames at 30 frames/s. The controller keeps `LEAD_FRAMES` (14) frames queued on the robot, regulated with `AnimationState`. | Verified by listening on one robot over Wi-Fi: smooth with a 14-frame lead, choppy with 3 or 8. Wi-Fi power saving on the computer caused gaps and should be disabled. WAV input is still expected at 22,050 or 48,000 Hz while the robot plays 22,320 samples per second. |
| Original animations | FlatBuffer clips, procedural face, head, lift, body and backpack-light keyframes are processed in `anim.PreprocessedClip`. A caller can filter keyframe types. `Client.play_anim_ppclip` places each keyframe on the frame of its trigger time. | `AnimFaceAnimation`, `AnimRobotAudio` and `AnimEvent` are not processed. Playing an unfiltered clip may move the robot. The extracted catalogue was preprocessed offline; that does not prove physical playback of every clip. |
| Original sounds | `audio.load_wem` and `Client.play_wem` decode a selected WEM through an external `vgmstream-cli`. | Resource files and decoder are supplied separately. Sound-event-to-WEM resolution, selection probabilities, mixing and synchronization with animation are not implemented. |
| Text-to-speech | `audio.synthesize_speech` and `Client.say_text` use a separately installed eSpeak NG executable. | Synthetic substitute voice, not the original Cozmo voice. WAV conversion and eSpeak output are tested offline; robot playback needs physical validation. |
| Motion detection | Optional `FaceVision` computes a changed-pixel score between consecutive grayscale frames. | The score is not an object tracker and also responds to head, robot, lighting and camera movement. The caller must decide how to use it. |
| Face detection | Optional OpenCV Haar cascade in `FaceVision`, with camera-event example. | A basic frontal detector; tests use controlled images or a stub detector, not an accuracy benchmark. It does not move the head or perform tracking itself. |
| Face recognition | Optional local LBPH model with explicit sample enrollment in `FaceRegistry`. | Basic image matching, not a reliable identity or liveness check. Sample quality, lighting and false positives need real-world evaluation. Face samples stay in the configured local directory. |
| Personality and behaviors | Legacy `Brain`, emotion and behavior modules load some configuration; `CompanionBrain` provides an independent decision policy. | Many original behaviors remain unimplemented. `CompanionBrain` only suggests actions; an application must provide fresh observations, execute actions and enforce motion safety. |

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
