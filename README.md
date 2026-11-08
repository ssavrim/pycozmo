
PyCozmo
=======

`PyCozmo` is a pure-Python communication library, alternative SDK, and application for the
[Cozmo robot](https://www.digitaldreamlabs.com/pages/cozmo) . It allows controlling a Cozmo robot directly, without
having to go through a mobile device, running the Cozmo app.

The library is loosely based on the [Anki Cozmo Python SDK](https://github.com/anki/cozmo-python-sdk) and the
[cozmoclad](https://pypi.org/project/cozmoclad/) ("C-Like Abstract Data") library.

This project is a tool for exploring the hardware and software of the Digital Dream Labs (originally Anki) Cozmo robot.
It is unstable and heavily under development.


Installation and development
----------------------------

Python 3.12 or newer is required. Install [uv](https://docs.astral.sh/uv/), then run:

```bash
uv sync
uv run pytest
```

For documentation tooling, use `uv sync --group docs`. To install the library in an
existing environment, use `python -m pip install .`.

Usage
-----

Basic:

```python
import time
import pycozmo

with pycozmo.connect() as cli:
    cli.set_head_angle(angle=0.6)
    time.sleep(1)
```

Advanced:

```python
import pycozmo

cli = pycozmo.Client()
cli.start()
cli.connect()
cli.wait_for_robot()

cli.drive_wheels(lwheel_speed=50.0, rwheel_speed=50.0, duration=2.0)

cli.disconnect()
cli.stop()
```


Documentation
-------------

[https://pycozmo.readthedocs.io/](https://pycozmo.readthedocs.io/)


Robot Support
-------------

A checked item means that an API or protocol implementation exists; it does not
mean that every robot, firmware version or peripheral has been tested. See the
[implementation audit](docs/robot_support_audit.md) for evidence and priorities.

Sensors:
- [x] Camera
- [x] Cliff sensor
- [x] Accelerometers
- [x] Gyro
- [x] Battery voltage
- [x] Cube battery voltage
- [x] Cube accelerometers
- [x] Backpack button (v1.5 hardware and newer)

Actuators:
- [x] Wheel motors
- [x] Head motor
- [x] Lift motor
- [x] OLED display
- [x] Speaker
- [x] Backpack LEDs
- [x] IR LED
- [x] Cube LEDs
- [x] Platform LEDs (when available)

On-board functions (see [docs/functions.md](docs/functions.md) for details):
- [x] Wi-Fi AP
- [x] Bluetooth LE
- [x] Localization
- [x] Path tracking
- [x] NV RAM storage
- [x] Over-the-air (OTA) firmware updates

Off-board functions (see [docs/offboard_functions.md](docs/offboard_functions.md) for details):
- [x] Procedural face generation
- [x] Cozmo animations from FlatBuffers .bin files (audio and some tracks are not played)
- [ ] Personality engine - partial, work in progress
- [ ] Cozmo behaviors - partial, work in progress
- [x] Basic camera motion detection (OpenCV; sensitive to camera motion)
- [ ] Visual object detection (cubes and charging platform; Bluetooth discovery is available)
- [ ] Cube marker recognition
- [x] Basic face detection (OpenCV Haar cascade; optional vision extra)
- [x] Basic local face recognition (OpenCV LBPH; optional vision extra)
- [ ] Facial expression estimation
- [ ] Pet detection
- [ ] Camera calibration
- [ ] Navigation map building
- [ ] Text-to-speech
- [ ] Songs

Extra off-board functions:
- [ ] Vector animations from FlatBuffers .bin files
- [ ] Vector behaviors
- [ ] ArUco marker recognition
- [ ] Cozmo and Vector robot detection
- [ ] Drivable area estimation
- [ ] Voice commands

If you have ideas for other functionality [share them via GitHub](https://github.com/ssavrim/pycozmo/issues).


Tools
-----

- [pycozmo_app.py](tools/pycozmo_app.py) - an alternative Cozmo application, implementing off-board functions
    ([video](https://youtu.be/gMEc6RzIm-E)).
- [pycozmo_dump.py](tools/pycozmo_dump.py) - a command-line application that can read and annotate Cozmo communication
    from [pcap files](https://en.wikipedia.org/wiki/Pcap) or capture it live using
    [pypcap](https://github.com/pynetwork/pypcap).
- [pycozmo_replay.py](tools/pycozmo_replay.py) - a basic command-line application that can replay .pcap files back to
    Cozmo.
- [pycozmo_anim.py](tools/pycozmo_anim.py) - a tool for examining and manipulating animation files.
- [pycozmo_update.py](tools/pycozmo_update.py) - a tool for over-the-air (OTA) updates of Cozmo's firmware.
- [pycozmo_protocol_generator.py](tools/pycozmo_protocol_generator.py) - a tool for generating Cozmo protocol encoder
    code.

**Note**: PyCozmo and `pycozmo_protocol_generator.py` in particular could be used as a base for creating a Cozmo protocol
encoder code generator for languages other than Python (C/C++, Java, etc.).
 

Examples
--------

Basic:
- [minimal.py](examples/minimal.py) - minimal code to communicate with Cozmo, using PyCozmo
- [extremes.py](examples/extremes.py) - demonstrates Cozmo lift and head control
- [backpack_lights.py](examples/backpack_lights.py) - demonstrates Cozmo backpack LED control
- [display_image.py](examples/display_image.py) - demonstrates visualization of image files on Cozmo's display
- [events.py](examples/events.py) - demonstrates event handling
- [camera.py](examples/camera.py) - demonstrates capturing a camera image 
- [go_to_pose.py](examples/go_to_pose.py) - demonstrates moving to a specific pose (position and orientation) 
- [path.py](examples/path.py) - demonstrates following a predefined path

Advanced:
- [display_lines.py](examples/display_lines.py) - demonstrates 2D graphics, using
    [PIL.ImageDraw](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html) on Cozmo's display
    ([video](https://youtu.be/ZT81PlmItrU))
- [rc.py](examples/rc.py) - turns Cozmo into an RC tank that can be driven with an XBox 360 Wireless controller or 
    Logitech Gamepad F310
- [video.py](examples/video.py) - demonstrates visualizing video captured from the camera back on display
- [cube_lights.py](examples/cube_lights.py) - demonstrates cube connection and LED control
- [cube_light_animation.py](examples/cube_light_animation.py) - demonstrates cube LED animation control
- [charger_lights.py](examples/charger_lights.py) - demonstrates Cozmo charging platform LED control
- [audio.py](examples/audio.py) - demonstrates 22 kHz, 16-bit, mono WAVE file playback through Cozmo's speaker 
- [nvram.py](examples/nvram.py) - demonstrates reading data from Cozmo's NVRAM (non-volatile memory)
- [procedural_face.py](examples/procedural_face.py) - demonstrates drawing a procedural face on Cozmo's display
- [procedural_face_show.py](examples/procedural_face_show.py) - demonstrates generating a procedural face
- [procedural_face_expressions.py](examples/procedural_face_expressions.py) - demonstrates displaying various facial
    expressions ([video](https://youtu.be/UyMTEOG2FyU))
- [anim.py](examples/anim.py) - demonstrates animating Cozmo


PyCozmo In The Wild
-------------------

- [Expressive Eyes](https://git.brl.ac.uk/ca2-chambers/expressive-eyes) - rendering various facial expressions using
    PyCozmo's procedural_face module
- an [application](https://github.com/paulbaumgarten/paulbaumgarten/blob/6d9025ec715e0b5f133ce9b7e39e74a5389c5334/myp/cozmo/assets/02-camera.py)
    that recognizes [ArUco markers](https://docs.opencv.org/master/d5/dae/tutorial_aruco_detection.html), using OpenCV
- a [ROS2 driver](https://github.com/solosito/cozmo_ros2_ws/)
- another [ROS2 driver](https://github.com/brean/cozmo_ros)


Connecting to Cozmo over Wi-Fi
------------------------------

A Wi-Fi connection needs to be established with Cozmo before using PyCozmo applications.

1. Wake up Cozmo by placing it on the charging platform
2. Make Cozmo display it's Wi-Fi PSK by rising and lowering its lift
3. Scan for Cozmo's Wi-Fi SSID (depends on the OS)
4. Connect using Cozmo's Wi-Fi PSK (depends on the OS)

[This video](https://www.youtube.com/watch?v=-k_oiQhBa5o) summarizes the connection process.


PyCozmo vs. the Cozmo SDK
-------------------------

A Cozmo SDK application (aka "game") acts as a client to the Cozmo app (aka "engine") that runs on a mobile device.
The low-level communication happens over USB and is handled by the `cozmoclad` library.

In contrast, an application using PyCozmo basically replaces the Cozmo app and acts as the "engine". PyCozmo handles
the low-level UDP communication with Cozmo.
   
```
+------------------+                   +------------------+                   +------------------+
|     SDK app      |     Cozmo SDK     |    Cozmo app     |       Cozmo       |      Cozmo       |
|      "game"      |     cozmoclad     |     "engine"     |      protocol     |     "robot"      |
|                  | ----------------> |   Wi-Fi client   | ----------------> |     Wi-Fi AP     |
|                  |        USB        |    UDP client    |     UDP/Wi-Fi     |    UDP Server    |
+------------------+                   +------------------+                   +------------------+
```



Requirements
------------

- Python 3.12 or newer.
- Pillow, NumPy, FlatBuffers and dpkt (installed automatically).
- Optionally, OpenCV with its contrib modules for face detection and recognition.

Installation
------------

Clone this fork and install it into a virtual environment:

```bash
git clone https://github.com/ssavrim/pycozmo.git
cd pycozmo
python -m venv .venv
# Activate .venv for your platform, then:
python -m pip install .
```

For face recognition, install `.[vision]` instead. For development with uv,
run `uv sync --extra vision` and see [CONTRIBUTING.md](CONTRIBUTING.md).
The PyPI package named `pycozmo` may be an older upstream release and does not
necessarily include this fork's changes.

Original animations and sounds are not distributed with the library. See the
resource and animation documentation before loading your own extracted files.


Support
-------

Bug reports and changes should be sent via GitHub:

[https://github.com/ssavrim/pycozmo](https://github.com/ssavrim/pycozmo)

DDL Robot Discord server, channel #development-cozmo:

[https://discord.gg/ew92haS](https://discord.gg/ew92haS)


Disclaimer
----------

This project is not affiliated with [Digital Dream Labs](https://www.digitaldreamlabs.com/) or
[Anki](https://anki.com/).

Original resources and cube connections
---------------------------------------

PyCozmo does not include Cozmo's proprietary resources. If you have extracted
animation files, load their directory explicitly with `cli.load_anims(path)` and
inspect `cli.anim_names`. `cli.play_anim(name)` may move the robot. To restrict a
clip to face and head tracks, use:

```python
from pycozmo import anim_encoder

cli.play_anim(name, allowed_types=(anim_encoder.AnimProceduralFace,
                                   anim_encoder.AnimHeadAngle))
```

Wwise `.wem` audio can be played with `cli.play_wem(path)` after installing
`vgmstream-cli` separately. Supply its executable path as `decoder=` if needed.
Only the audio file itself is decoded; animation sound-event mappings are not
played automatically. The default decoded duration limit is 60 seconds.
`cli.set_volume(level)` accepts an integer from 0 to 65535.

For cubes, `cli.available_objects` lists discovered factory IDs. Use
`cli.set_object_connection(factory_id)` to request a connection and wait for
`ObjectConnectionState` before addressing its reported `object_id`. Register
handlers before requesting a connection; events may arrive quickly. Some robots
reuse an object slot when another cube connects. See
[examples/cube_lights.py](examples/cube_lights.py) for an example.

The `pycozmo.companion` module provides a deterministic policy for observations
such as faces, cube taps and low battery. It returns suggested actions and never
commands motors. Applications must provide fresh observations, implement each
action, stop on `halt`, and enforce movement safety themselves; this is a policy
building block, not a complete autonomous controller.

For local face and motion detection, install the optional OpenCV dependency
with `uv sync --extra vision` (or `python -m pip install '.[vision]'` from the checkout).
`pycozmo.vision.FaceVision(callback, face_dir=...)` accepts PIL camera frames via
`submit`; the callback receives face rectangles, image dimensions, a monotonic
timestamp, a recognized name or `None`, and a motion score. Call `close()` when
finished. Recognition requires explicit `enroll(name)` with a recently detected
face. Samples are saved locally to `face_dir`, or by default under
`~/.local/share/pycozmo/faces`. `registry.clear()` removes managed samples and
preserves unrelated files in that directory.
Face and motion detection do not by themselves control the robot. See
[examples/face_detection.py](examples/face_detection.py) for connecting the
vision worker to camera events.
