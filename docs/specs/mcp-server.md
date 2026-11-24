# MCP server

An [MCP](https://modelcontextprotocol.io/) (Model Context Protocol) server that lets an AI assistant use a Cozmo robot:
look through the camera, move in short bounded steps, light the backpack and the cubes, play animations and talk.
Implemented by `pycozmo.mcp_server` and `tools/pycozmo_mcp.py`. It is not part of the original application: it is a way to
use PyCozmo.

Confidence: **Specified** (tested), **Validated** (done on a robot), **Not done**.

## Use

```bash
pip install pycozmo[mcp]            # the MCP SDK (2.x) is an optional dependency
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
| `stop` | Stop the wheels, head, lift and animation at once, also while a `drive`, `turn` or `play_animation` runs. |
| `move_head(angle_deg)` | Tilt the head, -25 to 44.5 degrees (values outside are limited). |
| `move_lift(height)` | Lift: 0 down, 1 fully up (values outside are limited). |
| `drive(distance_mm, speed_mmps)` | Straight, forward or backward. |
| `turn(angle_deg)` | Turn on the spot, positive counterclockwise. |
| `set_backpack_color(color)` | Backpack: off, red, green, blue, white. |
| `say(text)` | Speak a text of 1 to 300 characters (needs eSpeak NG on the computer). |
| `list_animations`, `play_animation(name, wheels)` | Animation groups of the prepared resources; plays one and waits for its end (20 s at most). Without its wheel movements unless `wheels` is true. |
| `camera_image` | A JPEG image of what the robot sees. |
| `find_markers` | Cubes and charger in the image now (needs the marker library of the prepared resources). |
| `connect_cube`, `set_cube_color(object_id, color)` | Connect the first cube that the robot sees; light it. |

## Safety

The robot is a physical object, so the movements are limited by the server, not by the assistant:

- A drive is at most 200 mm at 60 mm/s (40 mm/s by default; the robot can do 220), a turn at most 180 degrees at 60
  degrees per second, and the wheels never run more than 6 seconds in one call (`--max-speed` and `--max-drive` lower or
  raise the first two, up to 220 mm/s and 1000 mm). The wheels stop by themselves at the end of the movement. When the
  6 seconds are shorter than the movement asked, the answer gives the distance or angle of those 6 seconds, with
  `limited_by` "time".
- The wheels do not start when the robot is picked up or detects a cliff, and a movement in progress stops within 50 ms
  of a pick up, a cliff or a call of `stop` (`stopped_by` "picked up", "cliff" or "stop"), and when anything fails (the
  stop is sent in all cases). `stop` is served while a movement runs because the SDK 2.x runs each tool in a thread of
  its own; the SDK 1.x ran them one after the other, which is why 2.x is required.
- **Animations drive the wheels only when asked.** By default `play_animation` leaves out the body motion and
  turn-to-heading keyframes of the clip, whose speed and length none of the limits above bound; the head, lift, face,
  lights and sound play. With `wheels` true they play too, under the checks of a drive: refused with `--no-drive`, when
  the robot is picked up or at a cliff, and (edge guard) when a known edge is within reach of the body; the animation and
  the wheels stop within 50 ms of a pick up, a cliff or `stop`, and the wheels are stopped at its end. The distance that
  such an animation drives is not bounded beforehand. An unknown group, or a group none of whose clips can play now
  (cooldown, head angle), is refused at once.
- **Table edges** (`--no-edge-guard` turns it off; docs/specs/edges.md). The server keeps a `TableMemory` that the robot
  state feeds: the floor that the robot has driven over, and the drops that its cliff sensor saw. Backing up is limited to
  that floor (a robot that has not moved yet cannot back up: drive forward or turn first), forward driving stops before a
  known edge (and is refused when the edge is just ahead), turning on the spot is refused when a known edge is within
  reach of the body, and a backward move stops by itself when the floor behind it runs out (`stopped_by` "unknown floor").
  A robot that stopped at a cliff may still back up, within that floor, which is how it leaves the edge; it cannot drive
  forward or turn until the sensor no longer sees a drop. `status` gives `known_edges` and `backup_room_mm`; a `drive` that was shortened says why in `limited_by`. The memory is
  forgotten when the robot is picked up or when the connection is made again.
- There is no continuous driving, no path and no go-to-pose tool, and no tool that sends raw packets or changes a setting
  of the robot (cliff threshold, motor power, factory messages).
- Numbers that are not finite and text that is too long are refused; an unknown color is refused with the list of colors.
- What the assistant sees through the camera is sent to it. Do not point the robot at what must stay private.

A tool that fails returns an error with its reason (not connected, picked up, cliff, wheels disabled, no cube...).

## Not done

- Only the front cliff sensor exists, so the sides and the back of the robot are watched only through the floor that it
  has driven over.
- The server does not follow the battery: the assistant can read it in `status`. It does not drive the robot back to the
  charger.
- No streaming of images and no events: the assistant asks for an image when it wants one.
- No tool for the animation of the face, the lift to carry a cube, or the behaviors of the brain.
- Only the standard input and output transport is offered (no HTTP).
- The server sends a wheel command once and stops the wheels at the end of the movement. If its process is killed during
  a movement, that stop is not sent: what the robot does when the connection is lost was not measured.

## Validation

- Offline: the controller is tested with a fake robot (limits, refusals, stop on a cliff or a pick up during a movement,
  stop when the wheels fail, no drive mode, camera, cubes, animations without wheel keyframes, refusal of an unknown
  animation, animations with wheels (refusals, stop on a cliff or a pick up), distance and angle limited by the time,
  stop of a drive or an animation in progress), and the server with
  the installed SDK (the 16 tools, and a `stop` sent 0.3 s into a 5 s `drive` that ends it).
- On a robot (2026-11-23): an MCP client started the server by its standard input and output, and called `connect`,
  `status`, `move_head`, `set_backpack_color`, `camera_image` (a JPEG of 19.6 kB) and `disconnect`; the refusal of the wheels
  in `--no-drive` came back with its reason.
- On a robot, on the floor (2026-11-23): through the same MCP client, `drive` 50 mm forward then 50 mm backward and `turn` 90
  degrees, twice; the operator saw the movements as expected. The angles on the floor were not measured (the odometry read
  right after the stop is late and gave 33 degrees once, so it is not a measure).
- On a robot (2026-11-23): `drive` of 100 mm at 20 mm/s, the operator picked the robot up while it rolled: the wheels
  stopped (the robot had moved 22 mm), the answer was `stopped_by: "cliff"` (the cliff sensor reacted before the picked up
  flag), `status` showed `cliff_detected`, and the next `drive` was refused with "A cliff is detected".
- On a robot, on a table (2026-11-23, through the MCP client, the operator did not touch the robot): the robot was put about
  15 cm from the edge, facing it. `status` showed no known edge and a backup room of 0, and `drive` backward was refused (unknown
  floor). Two forward `drive` calls at 15 mm/s stopped at the drop after 168.6 mm (`stopped_by` cliff): one edge known, a
  backup room of 100 mm. At the edge `drive` forward and `turn` were refused ("it can only back up"). `drive` of -100 mm then ran
  in full, and `turn` of 90 degrees was allowed. The wheels stayed on the table all along.
- Two defects were found and fixed with this test: a robot at the edge could not back up (every move was refused), and a
  backward move stopped after 31 mm on 205 mm of known floor, because the drift of the heading (up to 6 degrees) moved the rear
  corners out of the strip by more than 5 mm; the lateral tolerance of the backward check is now 10 mm (checked on the floor,
  then on the table). This is one robot on one table: the margins were not measured on other surfaces or edges.
