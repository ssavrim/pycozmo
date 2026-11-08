
Overview
========

https://github.com/zayfod/pycozmo

`PyCozmo` is a pure-Python communication library, alternative SDK, and application for the
[Cozmo robot](https://www.digitaldreamlabs.com/pages/cozmo) . It allows controlling a Cozmo robot directly, without
having to go through a mobile device, running the Cozmo app.

The library is loosely based on the [Anki Cozmo Python SDK](https://github.com/anki/cozmo-python-sdk) and the
[cozmoclad](https://pypi.org/project/cozmoclad/) ("C-Like Abstract Data") library.

This project is a tool for exploring the hardware and software of the Digital Dream Labs (originally Anki) Cozmo robot.
It is unstable and heavily under development.


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
run `uv sync --extra vision` and see [CONTRIBUTING.md](https://github.com/ssavrim/pycozmo/blob/master/CONTRIBUTING.md).
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
