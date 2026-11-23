Revision History
================

Unreleased
----------
- The MCP server uses the memory of the table edges (pycozmo.edges): it backs up only onto floor that the robot has driven
    over, drives forward only until a known edge, turns on the spot only clear of a known edge, and stops a backward move when
    the floor runs out (`--no-edge-guard` turns it off). `status` reports `known_edges` and `backup_room_mm`.
- The validation tool (tools/pycozmo_validate.py) writes on the screen of the robot what the operator has to do or to watch
    (lift me, wake a cube, table edge, hand under me...), with the head raised so that it can be read.
- Add an MCP server (pycozmo.mcp_server, tools/pycozmo_mcp.py, docs/specs/mcp-server.md; `pip install pycozmo[mcp]`) that
    lets an AI assistant connect to the robot, read its state, move the head and lift, drive and turn in short bounded steps
    that stop on a pick up or a cliff, light the backpack and the cubes, speak, play animations and get a camera image.
- Add pycozmo.edges (docs/specs/edges.md), ported from Cozmonaute: TableMemory remembers the drops that the cliff sensor saw
    and the floor that the robot stood on (observe, backup_room, can_turn, clear_ahead), and CliffRetreat is the reflex that
    backs off and turns away from a drop.
- Integrate the cube light animations and the markers in Brain: a cube that connects plays the animation `Connected`
    (`CubeLightManager`, `Brain.play_cube_animation`); with `Brain(cli, markers=True)`, a thread (`MarkerWatcher`) looks for
    markers in the camera images and a cube or the charger that appears posts `ObjectPositionUpdated`; a cube that moves
    posts `CubeMoved`. The detector no longer emits warnings on a collapsed frame.
- Declare the 13 remaining messages of the original application, which have arrays or strings (WriteBodyStorage,
    PrintText, DataDump, TimeProfileStat, IMUDataChunk, DefaultCameraParams, CrashReport, FWVersionInfo...), read from the
    unpacking functions of the original: a length on 1 byte for arrays and strings. All the messages of the original are now declared except FlashObjectIDs, which
    had no visible effect on a cube.
- Name the fields of the messages from the C# code of the application (CliffEvent, PowerState, IMURawDataChunk...), and
    of TurnInPlace (num_half_revolutions), AppendPathSegPointTurn (use_shortest_direction), SetOrigin (timestamp,
    pose_angle_rad), HardwareInfo (hw_version, one uint16 instead of two uint8) and FirmwareSignature (robot_id). The
    keywords unknown* of these fields no longer exist; the wire format is the same. IMUDataChunk and IMUTemperature hold
    floats.
- Declare 51 messages of the original application that PyCozmo did not (docs/specs/protocol-gaps.md): commands of the
    robot (TurnInPlaceAtAngularSpeed, IMURequest, SetCliffDetectThreshold, EnableMotorPower, EnableBraceWhenFalling,
    the factory and test ones...) and messages from the robot (IMURawDataChunk, CliffEvent, MotorAutoEnabled,
    RobotStopped...), with the layout of the original; the fields are named only where the name of the message says
    what they are. A packet with one of these numbers is now decoded instead of being an UnknownCommand. The messages
    of the group factory must not be sent. Add a reference of the numbers, names and sizes with a test.
- Add pycozmo.markers (docs/specs/markers.md): recognize the markers of Cozmo (faces of the cubes, charger, custom
    object markers) in an image, with MarkerDetector, and estimate_marker_pose (sizes of the cube and charger markers
    included); tools/pycozmo_markers_eval.py measures it on synthetic images. The library of symbols is read from the
    engine library of the APK by pycozmo_resources.py prepare (3.4.0 only; a warning when it is not available) and kept
    in the resources of the user as cozmo_resources/config/engine/vision/markers.npz.
- Add the SetCubeGamma command (0x0c) and send it from Client.set_cube_lights before the first lights of a cube, and
    again after the cube connects again, as the original application does (without it white shows as yellow and
    red). New parameter gamma (default CUBE_GAMMA, None to not send it).
- Client.set_all_backpack_lights turns the left and right backpack LEDs off for white and grey: they are red only and
    showed red next to a white line. Add lights.is_white_shade and lights.side_light. Other colors are unchanged.
- Use the needs, the mood, the reaction triggers and the behavior selection in Brain: it updates the needs, runs the
    reaction that ReactionTriggerManager fires (Frustration now picks the minor or the major reaction from the
    Confident emotion), lets a reaction interrupt the running behavior, and, with Brain(cli, freeplay=True), chooses
    the next behavior of the freeplay when nothing runs. Brain.reaction_trigger_beahvior_map is replaced by
    Brain.reaction_manager. Add select_reaction_trigger.
- Add pycozmo.cube_lights (docs/specs/cube-light-patterns.md): read the cube light animations and the cube animation
    triggers of the prepared resources, convert a pattern to the four LightState of a cube and its rotation period,
    and play an animation with CubeLightPlayer (durations, resume of the previous animation, canBeOverridden). The
    color and frame conversions are in pycozmo.lights (encode_color, white_balance, ms_to_frames, make_light_state,
    Color.to_int16). Breaking: Color.to_int16 now encodes as the original application does (channels divided by 8, bit
    15 set when the alpha is not 0, which is the case of the colors made from an RGB value), so a few values differ by
    one step from before; Color.to_int16(balance=True) adds the white balance of the cube lights, which the backpack
    lights do not use.
- Decide which reaction triggers fire (docs/specs/reaction-triggers.md): ReactionTriggerManager, with the
    parameters of FistBump and Frustration read by ReactionTrigger, and load_reaction_triggers, which keeps the
    entries that share a trigger (load_reaction_trigger_behavior_map keeps the last one). SelectionContext gains the
    state that the triggers need. The decision starts no behavior.
- Choose the next behavior (docs/specs/behavior-selection.md): Activity.choose() and the new StrictPriorityActivity
    choose among the sub-activities and the behaviors with their strategies (ActivityStrategy), from a
    SelectionContext; the choice starts no behavior. BehaviorChooser follows the specification: the repetition penalty
    is evaluated at the time since the behavior stopped (it was a count), with the penalty of a running behavior,
    the random value and the bonus of the behavior that runs; apply_repetition_penalty() now means "the behavior
    stopped". Breaking: BehaviorChooser loses behavior_scores, behavior_names, behavior_repetitions, total_score and
    repetition_penalties. Add behavior.BehaviorConfig and behavior.load_behavior_configs.
- Add pycozmo.needs.NeedsModel (repair, energy and play levels with decay by band, modifiers, brackets,
    fullness cooldown, actions and broken parts) and pycozmo.emotions.Mood, driven by the needs and mood
    configuration of the prepared resources (docs/specs/needs.md, docs/specs/mood.md).
- Fix emotions (pycozmo.emotions): there are nine emotion types, not seven (Charged and Winning were missing);
    the decay follows the graph from the last strengthening instead of restarting at every change; emotion events
    apply their repetition penalty. Brain applies emotion events through Mood. EmotionType accepts an injectable
    clock. Breaking: the decay of an emotion now continues after an add() instead of restarting, unless the
    value is strengthened by more than 0.05 or changes sign; EmotionType loses its last_change and last_value
    attributes (the decay is no longer computed from them).
- Depend on opencv-contrib-python-headless (4.12 to 5.x) in place of opencv-python: face
    recognition and, from OpenCV 5, face detection need the contrib modules, and PyCozmo uses no
    OpenCV window. Breaking: remove opencv-python from environments that had it, as both
    provide the cv2 module.
- Fix Client.lift_position: the lift_height_mm field of RobotState is the angle of the lift arm in
    radians, not a height; the position was read as a few millimeters. Breaking: the field is renamed
    to lift_height_rad (the encoded bytes are unchanged).
- Report the end of an animation from the frame count: the robot does not send AnimationEnded
    for animations with sound, so EvtAnimationCompleted was never dispatched. The AnimationEnded
    that answers the cancel of the previous animation no longer ends the next one.
- Fix the animation frame layout: keyframes whose trigger times fall in the same 1/30 s frame
    share it (one frame per keyframe stretched clips and put silence between audio frames), and
    frames are no longer sent in bursts while an animation is streamed.
- Fix animation stream messages (docs/specs/animation-stream.md):
    AnimHead and AnimLift durations are 16-bit (the former variability byte was the high
    byte), so moves longer than 255 ms are no longer truncated; head/lift variability is
    drawn on the host. AnimBody's second field is the curvature radius: body motions are
    sent as-is (straight, in place, or arc) and stopped at the end, instead of host-computed
    wheel speeds with a wrong arc formula. Breaking: AnimHead/AnimLift lose the
    variability argument and AnimBody.unknown becomes curvature_radius_mm.
- Add AnimEvent (0x95) and AnimationEvent (0xd5); stream event keyframes and dispatch
    EvtAnimationEvent when the robot plays them.
- Add pycozmo.anim_library.AnimationLibrary: clips, animation groups and triggers of the
    prepared resources, with the selection of docs/specs/animation-library.md (shared per-clip cooldowns,
    head angle ranges in radians, mood and fallbacks). AnimationGroup.choose_member and
    Client.play_anim_group use it; groups whose weights are all zero no longer fail.
- Play face animation keyframes (PNG frames, one per animation frame) in
    PreprocessedClip; they replace procedural faces while they play.
- Play the sound of original animations: pycozmo.audiokinetic.hierarchy reads Wwise
    version 120 banks (events, actions, sounds, containers, properties, embedded media);
    pycozmo.anim_audio chooses the event of each RobotAudio keyframe with its
    probabilities, resolves it (random/shuffle/sequence containers, avoid-repeat, switches,
    layers, volume, pitch, filters, delays, loops, Stop, Seek), decodes media with
    vgmstream-cli and mixes them at 22,320 Hz into OutputAudio packets.
    AnimationLibrary.enable_audio() turns it on.
- Prepare resources from the user's copy of the Cozmo application (XAPK, APK and OBB,
    or OBB alone) with pycozmo.resources.prepare() and 'pycozmo_resources.py prepare':
    version and signer checks, safe extraction (no path traversal, links or archive bombs),
    atomic replacement, and a manifest with checksums checked by 'verify'. Sound banks go
    to cozmo_resources/sound/AudioAssets. The automatic download from a third-party mirror,
    which disabled TLS certificate verification, is removed. See docs/specs/resources.md.
- Correct procedural face rendering for inverted shapes in original animations.
- Clamp u-law output to valid bytes and decode PCM as little-endian.
- Wait reliably for robot initialization and handle reused cube object slots.
- Load animations from a custom directory and optionally filter keyframe types.
- Add optional WEM decoding through an externally installed vgmstream-cli.
- Add a deterministic companion decision policy independent of robot I/O.
- Add optional local face recognition and motion detection via OpenCV.
- Add optional eSpeak NG text-to-speech and cube light/accelerometer helpers.
- Encode audio in the robot's 8-bit format without mu-law bias or +1 offset: silence
    no longer becomes a +/-132 square wave and loud positive samples no longer flip sign.
- Keep a 14-frame lead of animation frames on the robot, regulated with
    AnimationState, so Wi-Fi jitter no longer interrupts audio playback.
- Fix play_anim_ppclip padding: it inserted an empty frame after every keyframe,
    interleaving animation audio with silence and slowing dense clips.
- Detect body orientation from gravity (IMU reading rotated by the head angle) with
    hysteresis; the pose heading no longer reports the robot as lying on a side.
    Add RobotOrientation.UPSIDE_DOWN.
- Add EvtRobotTapped, detected from IMU readings while the robot is still, and
    Client.battery_percent, an indicative charge estimate (pycozmo.sensors).
- Fixed all mypy findings and made mypy checks required in CI. Modernized type hints for Python 3.11+.
- Fixed animation controller status change handlers failing on every animation status change, which aborted
    processing of the rest of the robot state update.
- Fixed AnimLiftHeight.from_dict() failing when the height variability is not specified.
- Fixed the default name of unnamed lights.Color objects.
- Fixed the maximum number of random completions of activity objectives and StrictPriority behavior choosers failing
    before a reset.
- Animation group members with null head angle limits no longer fail to load.
- Malformed SoundbanksInfo.xml files now raise AudioKineticFormatError.
- pycozmo_resources.py no longer disables TLS certificate verification for downloads.
- Pillow 9.1 or newer is now required.
- Fixed event handlers not being removed when Dispatcher.del_handler() is given the handler function.
- Fixed event handlers being skipped when they follow a one-shot handler, e.g. one registered by wait_for().
- Fixed images displayed with Client.display_image() being replaced by procedural face on the next frame, which
    made the duration parameter ineffective (thanks to Aitor Miguel Blanco / gimait)
- Renamed BodyColor.DEV to BodyColor.WHITE_v17. Body color 5 is used by Cozmo HW v1.7.

v0.9.0 (Nov 15, 2026)
---------------------
- Dropped support for Python 3.6 - 3.10 and added support for Python 3.11 - 3.15.
- Replaced setup.py and requirements files with pyproject.toml.
- Replaced the standard library chunk module, removed in Python 3.13, with a built-in implementation
    (thanks to Hossam Khader / hossamkhader)
- Updated FlatBuffers usage to the 2.x API. FlatBuffers 2.0 or newer is now required.
    (thanks to Hossam Khader / hossamkhader)
- Updated flake8 and mypy configuration and fixed lint findings.
- Switched documentation to the MyST Markdown parser and added Read the Docs configuration.
- Added OpenCV as a dependency.
- Reading of saved cube IDs from robot NV storage. Exposed as Client.saved_objects .
- Reading of camera calibration from robot NV storage. Exposed as Client.camera_config, with a camera matrix
    suitable for use with OpenCV.
- Emotion type value updates and emotion events, applied by the brain on reaction triggers.

v0.8.0 (Nov 12, 2020)
---------------------
- New animation controller that synchronizes animations, audio playback, and image displaying.
- Procedural face generation to bring the roboto to life.
- Loading of Cozmo resource files - activities, behaviors, emotions, light animations
    (thanks to Aitor Miguel Blanco / gimait)
- pycozmo.audiokinetic module for working with Audiokinetic WWise SoundBank files.
- New tool for managing Cozmo resources - pycozmo_resources.py .
- Initial Cozmo application with rudimentary reactions and behaviors - pycozmo_app.py .
- Cozmo protocol client robustness improvements.
- CLAD encoding optimizations.
- Cliff detection and procedural face rendering improvements (thanks to Aitor Miguel Blanco / gimait)
- Replaced pycozmo.run_program() with pycozmo.connect() context manager. 
- Renamed the NextFrame packet to OutputSilence to better describe its function.
- Dropped support for Python 3.5 and added support for Python 3.9.
- Bug fixes and documentation improvements.

v0.7.0 (Sep 26, 2020)
---------------------
- Full robot audio support and a new AudioManager class (thanks to Aitor Miguel Blanco / gimait).  
- Cozmo protocol client robustness improvements (thanks to Aitor Miguel Blanco / gimait):
    - frame retransmission
    - transmission of multiple packets in a single frame
- Procedural face rendering improvements (thanks to Catherine Chambers / ca2-chambers).
- Added support for robot firmware debug message decoding.
- Added a new video.py example.
- Added a tool for over-the-air firmware updates - pycozmo_update.py (thanks to Einfari).
- Added hardware version description.
- Bug fixes and documentation improvements.

v0.6.0 (Jan 2, 2020)
--------------------
- Improved localization - SetOrigin and SyncTime commands and pose (position and orientation) interpretation.
- Added new path tracking commands (AppendPath*, ExecutePath, etc.) and examples (path.py, go_to_pose.py). 
- Added support for drawing procedural faces (thanks to Pedro Tiago Pereira / ppedro74).
- Added support for reading and writing animations in FlatBuffers (.bin) and JSON format.
- Added a new tool for examining and manipulating animation files - pycozmo_anim.py .
- Added commands for working with cube/object accelerometers - StreamObjectAccel, ObjectAccel.
- Improved function description. 
- Bug fixes and documentation improvements.

v0.5.0 (Oct 12, 2019)
---------------------
- Added initial client API.
- Separated low-level Cozmo connection handling into a new ClientConnection class.
- Improved the ImageDecoder class and added a new ImageEncoder class for handling Cozmo protocol image encoding.
- Added new examples for displaying images from files and drawing on Cozmo's OLED display.
- New protocol commands: EnableBodyACC, EnableAnimationState, AnimHead, AnimLift, AnimBackpackLights, AnimBody,
    StartAnimation, EndAnimation, AnimationStarted, AnimationEnded, DebugData.
- Initial support for Cozmo animations in FlatBuffer .bin files.
- Improved filtering through packet groups for pycozmo_dump.py and pycozmo_replay.py .
- Added type hints in the protocol generator.
- Bug fixes and documentation improvements.

v0.4.0 (Sep 13, 2019)
---------------------
- New commands: Enable, TurnInPlace, DriveStraight, ButtonPressed, HardwareInfo, BodyInfo, EnableColorImages,
    EnableStopOnCliff, NvStorageOp, NvStorageOpResult, FirmwareUpdate, FirmwareUpdateResult.
- New events: AnimationState, ObjectAvailable, ImageIMUData.
- New examples: cube_lights.py, charger_lights.py, cube_light_animation.py.
- Improved handling of 0x04 frames
- Added support for Int8, Int32, and enumeration packet fields.
- Improved robot state access.
- Added object availability and animation state access.
- Added initial pycozmo_replay.py tool for replaying PCAP files to Cozmo.
- Added OLED display initial image encoder code. 
- Added initial function description.

v0.3.0 (Sep 1, 2019)
--------------------
- Camera control and image reconstruction commands.
- Initial robot state commands (coordinates, orientation, track speed, battery voltage).
- Cube control commands.
- Fall detection commands.
- Audio volume control command.
- Firmware signature identification commands.
- Improved logging control.
- Python 3.5 compatibility fixes (thanks to Cyke).

v0.2.0 (Aug 25, 2019)
---------------------
- Backpack light control commands and example.
- Raw display control commands and example.
- Audio output commands and example.

v0.1.0 (Aug 15, 2019)
---------------------
- Initial release.
