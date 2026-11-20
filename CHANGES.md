Revision History
================

Unreleased
----------
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
- Require Python 3.12 or later; add uv project management and Python 3.12/3.13/3.14 CI.
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
