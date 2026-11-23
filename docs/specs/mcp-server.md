# MCP server

An [MCP](https://modelcontextprotocol.io/) (Model Context Protocol) server that lets an AI assistant use a Cozmo robot:
look through the camera, move in short bounded steps, light the backpack and the cubes, play animations and talk.
Implemented by `pycozmo.mcp_server` and `tools/pycozmo_mcp.py`. It is not part of the original application: it is a way to
use PyCozmo.

Confidence: **Specified** (tested), **Validated** (done on a robot), **Not done**.

## Use

```bash
pip install pycozmo[mcp]            # the MCP SDK is an optional dependency
pycozmo_mcp.py [--no-drive] [--max-speed MMPS] [--max-drive MM]
```

The computer must be on the Wi-Fi network of the robot (`Cozmo_XXXXXX`) when the assistant calls `connect`. The server
talks on its standard input and output: **nothing listens on the network**. To give it to an assistant, declare it as an
MCP server of the client; for Claude Code:

```bash
claude mcp add pycozmo -- /path/to/.venv/bin/python /path/to/tools/pycozmo_mcp.py
```

Other clients take the same command in their configuration (`"command"` and `"args"`). `--no-drive` disables the wheels
(looking, talking, lights, head and lift still work): use it for a first try.

## Tools

| Tool | What it does |
|---|---|
| `connect`, `disconnect` | Connect to the robot and return its state; stop and disconnect. |
| `status` | Battery, pose, head, flags (on the charger, picked up, cliff detected, moving), cubes seen and connected. |
| `stop` | Stop the wheels, head and lift at once. |
| `move_head(angle_deg)` | Tilt the head, -25 to 44.5 degrees (values outside are limited). |
| `move_lift(height)` | Lift: 0 down, 1 fully up (values outside are limited). |
| `drive(distance_mm, speed_mmps)` | Straight, forward or backward. |
| `turn(angle_deg)` | Turn on the spot, positive counterclockwise. |
| `set_backpack_color(color)` | Backpack: off, red, green, blue, white. |
| `say(text)` | Speak a text of 1 to 300 characters (needs eSpeak NG on the computer). |
| `list_animations`, `play_animation(name)` | Animation groups of the prepared resources; plays one and waits for its end (20 s at most). |
| `camera_image` | A JPEG image of what the robot sees. |
| `find_markers` | Cubes and charger in the image now (needs the marker library of the prepared resources). |
| `connect_cube`, `set_cube_color(object_id, color)` | Connect the first cube that the robot sees; light it. |

## Safety

The robot is a physical object, so the movements are limited by the server, not by the assistant:

- A drive is at most 200 mm at 60 mm/s (40 mm/s by default; the robot can do 220), a turn at most 180 degrees at 60
  degrees per second, and the wheels never run more than 6 seconds in one call (`--max-speed` and `--max-drive` lower or
  raise the first two, up to 220 mm/s and 1000 mm). The wheels stop by themselves at the end of the movement.
- The wheels do not start when the robot is picked up or detects a cliff, and a movement in progress stops within 50 ms
  of a pick up or a cliff, and when anything fails (the stop is sent in all cases).
- There is no continuous driving, no path and no go-to-pose tool, and no tool that sends raw packets or changes a setting
  of the robot (cliff threshold, motor power, factory messages).
- Numbers that are not finite and text that is too long are refused; an unknown color is refused with the list of colors.
- What the assistant sees through the camera is sent to it. Do not point the robot at what must stay private.

A tool that fails returns an error with its reason (not connected, picked up, cliff, wheels disabled, no cube...).

## Not done

- The server does not follow the battery: the assistant can read it in `status`. It does not drive the robot back to the
  charger.
- No streaming of images and no events: the assistant asks for an image when it wants one.
- No tool for the animation of the face, the lift to carry a cube, or the behaviors of the brain.
- Only the standard input and output transport is offered (no HTTP).

## Validation

- Offline: the controller is tested with a fake robot (limits, refusals, stop on a cliff or a pick up during a movement,
  stop when the wheels fail, no drive mode, camera, cubes), and the server with the installed SDK (the 16 tools).
- On a robot (2026-11-23): an MCP client started the server by its standard input and output, and called `connect`,
  `status`, `move_head`, `set_backpack_color`, `camera_image` (a JPEG of 19.6 kB) and `disconnect`; the refusal of the wheels
  in `--no-drive` came back with its reason.
