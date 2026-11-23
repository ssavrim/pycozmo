# Protocol gaps

The messages that the original application exchanges with the robot, compared with the ones that PyCozmo declares
(`pycozmo/protocol_declaration.py`). Machine-readable reference: `pycozmo/tests/protocol_reference.json`, checked by
`pycozmo/tests/test_protocol_reference.py`.

Confidence: **Specified** (precise, tested), **Assumed** (reasonable choice), **Not validated** (declared from the
original application but not tried on a robot), **Not modeled**.

## Source

The original application has 82 messages to the robot and 54 from the robot, each with a number, a name and, for most,
a fixed size. They are the numbers of the packets of the protocol (`docs/protocol.md`): the numbers that PyCozmo already
used are the same, so they are the same messages. The layout of a message (the order and the size of its fields) comes
from the way the original application reads it, and the types of the fields from the way it compares two messages:
float, signed or unsigned integer, boolean. The original does not keep the names of the fields.

## What was compared

- **Sizes** (Specified, tested). Of the messages that both declare, 62 have a fixed size in both, and all are equal, except
  `ShutdownRobot` (0xa9): the original has a payload of 32 bytes, PyCozmo declares none. It is not declared further because
  the content of the payload was not recovered.
- **Names** (Specified). PyCozmo kept 21 names that are not the ones of the original: they are listed below. They are
  kept so as not to break the applications that use them; the numbers and the sizes are the same. Most are the same
  message under another name (`LightStateCenter` is `BackpackLightsMiddle`, `LightStateSide` is `BackpackLightsTurnSignals`,
  `SetRobotVolume` is `SetAudioVolume`, `EnableCamera` is `ImageRequest`). Two deserve a note:
  - 0x33 is `DriveWheelsCurvature` in the original (speed, acceleration and a curvature radius in millimeters); PyCozmo
    calls it `TurnInPlaceAtSpeed` and names the last field `direction`: it turns in place with the radius that the original
    reserves for that.
  - The original `TurnInPlaceAtSpeed` is 0x3a (two floats, an angular speed and what follows); it is declared as
    `TurnInPlaceAtAngularSpeed` because the name is taken.

| Number | Original name | PyCozmo name | Size |
|---|---|---|---|
| 0x03 | BackpackLightsMiddle | LightStateCenter | variable |
| 0x05 | SetPropSlot | ObjectConnect | 5 |
| 0x11 | BackpackLightsTurnSignals | LightStateSide | variable |
| 0x25 | GetManufacturingInfo | Enable | 0 |
| 0x33 | DriveWheelsCurvature | TurnInPlaceAtSpeed | 10 |
| 0x39 | SetBodyAngle | TurnInPlace | 20 |
| 0x3d | AppendPathSegmentLine | AppendPathSegLine | 28 |
| 0x3e | AppendPathSegmentArc | AppendPathSegArc | 32 |
| 0x3f | AppendPathSegmentPointTurn | AppendPathSegPointTurn | 29 |
| 0x45 | AbsoluteLocalizationUpdate | SetOrigin | 24 |
| 0x49 | CarryStateUpdate | SetCarryState | 1 |
| 0x4c | ImageRequest | EnableCamera | 2 |
| 0x64 | SetAudioVolume | SetRobotVolume | 2 |
| 0xb0 | PrintTrace | DebugData | variable |
| 0xb9 | ObjectTappedFiltered | ObjectTapFiltered | 10 |
| 0xc2 | SyncTimeAck | RobotDelocalized | 0 |
| 0xc4 | MotorActionAck | AcknowledgeAction | 1 |
| 0xc9 | RobotAvailable | HardwareInfo | 6 |
| 0xdb | BackpackButton | ButtonPressed | 1 |
| 0xed | ManufacturingID | BodyInfo | 12 |
| 0xee | FirmwareVersion | FirmwareSignature | variable |

## Messages declared by this change

51 messages were not declared and are now (the original name is given when PyCozmo's differs). The fields are named only
where the name of the message says what they are (`threshold`, `enable`, `angle_rad`, `motor_id`...): the others are
`unknown0`, `unknown1`... with their type. **Not validated**, unless the next section says so.

| Number | Name | Direction | Size | Group |
|---|---|---|---|---|
| 0x01 | AdjustTimestamp | engine to robot | 4 | system |
| 0x02 | BackpackSetLayer | engine to robot | 1 | lights |
| 0x06 | KillBodyCode | engine to robot | 0 | factory |
| 0x07 | SetBodyRadioMode | engine to robot | 2 | factory |
| 0x0e | ReadBodyStorage | engine to robot | 2 | factory |
| 0x12 | SendDTMCommand | engine to robot | 16 | factory |
| 0x24 | GetBodySerialNumber | engine to robot | 0 | factory |
| 0x38 | HeadAngleUpdate | engine to robot | 4 | motors |
| 0x3a | TurnInPlaceAtAngularSpeed (original: TurnInPlaceAtSpeed) | engine to robot | 8 | motors |
| 0x44 | PlaceObjectOnGround | engine to robot | 25 | docking |
| 0x46 | StartControllerTestMode | engine to robot | 13 | factory |
| 0x47 | ControllerGains | engine to robot | 17 | factory |
| 0x4a | IMURequest | engine to robot | 4 | system |
| 0x4d | FlashObjectIDs | engine to robot | 4 | objects |
| 0x4e | ObjectBeingCarried | engine to robot | 5 | localization |
| 0x50 | EnableMotorPower | engine to robot | 2 | motors |
| 0x51 | SetMotionModelParams | engine to robot | 4 | motors |
| 0x52 | EnterSleepMode | engine to robot | 0 | system |
| 0x53 | PowerState | engine to robot | 17 | system |
| 0x54 | SetCliffDetectThreshold | engine to robot | 2 | system |
| 0x55 | EnableReadToolCodeMode | engine to robot | 9 | docking |
| 0x59 | RollActionParams | engine to robot | 20 | docking |
| 0x5a | CameraFOVInfo | engine to robot | 8 | camera |
| 0x61 | ForceDelocalizeSimulatedRobot | engine to robot | 0 | localization |
| 0x62 | ObjectConnectionStateToRobot | engine to robot | 13 | objects |
| 0x63 | EnableBraceWhenFalling | engine to robot | 1 | system |
| 0x65 | GenerateTestTone | engine to robot | 0 | system |
| 0x80 | RequestCrashReports | engine to robot | 4 | system |
| 0x82 | EnableWiFiTelemetry | engine to robot | 0 | system |
| 0x89 | DebugSetRTTO | engine to robot | 2 | system |
| 0x96 | AnimEventToRTIP | engine to robot | 2 | anim |
| 0xa2 | EnterFactoryTestMode | engine to robot | 8 | factory |
| 0xa3 | DisplayNumber | engine to robot | 7 | factory |
| 0xa5 | BodySerialNumber | engine to robot | 4 | factory |
| 0xab | AppConnectConfigFlags | engine to robot | 17 | factory |
| 0xac | AppConnectConfigIPInfo | engine to robot | 13 | factory |
| 0xad | AppConnectGetRobotIP | engine to robot | 1 | factory |
| 0xb2 | MainCycleTimeError | robot to engine | 16 | debug |
| 0xba | RampTraverseStart | robot to engine | 4 | docking |
| 0xbb | RampTraverseComplete | robot to engine | 5 | docking |
| 0xbc | BridgeTraverseStart | robot to engine | 4 | docking |
| 0xbd | BridgeTraverseComplete | robot to engine | 5 | docking |
| 0xc0 | CliffEvent | robot to engine | 6 | state |
| 0xc1 | PotentialCliff | robot to engine | 0 | state |
| 0xd4 | RobotStopped | robot to engine | 1 | state |
| 0xd6 | FactoryTestParameter | robot to engine | 4 | factory |
| 0xd8 | MotorAutoEnabled | robot to engine | 2 | motors |
| 0xd9 | RobotErrorReport | robot to engine | 5 | state |
| 0xdc | IMUTemperature | robot to engine | 4 | state |
| 0xec | WiFiFlashID | robot to engine | 4 | factory |

The messages of the group `factory` (`KillBodyCode`, `SetBodyRadioMode`, `ReadBodyStorage`, `SendDTMCommand`,
`StartControllerTestMode`, `ControllerGains`, `EnterFactoryTestMode`, `DisplayNumber` and the `AppConnect` ones) are
for the factory or for tests of the original application; they are declared so that the packets can be read in a capture,
and **must not be sent to a robot**.

## Validated on a robot

- `IMURequest` (0x4a): the field is the duration of the stream, in milliseconds. Asking for 500, 2000 and 4000 ms gave about
  100, 400 and 800 `IMURawDataChunk` (0xc7), about 180 per second.
- `IMURawDataChunk` (0xc7): 14 bytes (the size of the original), six signed 16-bit values and two bytes. The first three
  are about 0 at rest and move when the robot is turned (gyroscope); the next three keep a magnitude of about 10450 whatever the
  tilt (0.3 % of variation over 1200 samples) and their components follow the tilt (accelerometer, 1 g is about 10450).
  The last two bytes are not understood. The unit of the values is not known.
- `TurnInPlaceAtAngularSpeed` (0x3a): sent every 100 ms for 3 s with `speed_rad_per_sec` 0.5 and `accel_rad_per_sec2` 0, the robot turned
  on the spot (about a quarter of a turn seen on the floor) and stopped after `stop_all_motors`. The odometry gave 70 degrees
  for 86 expected: the start-up ramp is not accounted for. The acceleration was 0: the robot still turned, so 0 probably means a default.
- `MotorAutoEnabled` (0xd8): received twice while the robot was lifted, `motor_id` `MOTOR_LIFT`, `enabled` False. The
  motor identifier is decoded.
- `CliffEvent` (0xc0) and `RobotStopped` (0xd4): received while the robot drove at 20 mm/s and was lifted from
  the floor (not by lifting it at rest): `CliffEvent` x5 (`timestamp` 1650, `detected_flags` 3, `did_stop_for_cliff` True) and `RobotStopped`
  x3 (`unknown0` 1). `RobotStopped` has no named field in the application. `PotentialCliff` was not received.
- `SetCliffDetectThreshold` (0x54): sent with 0, 100, 300, 600, 1000, 1500, 2500, 4000 and 0 to a robot at rest on the floor.
  The robot kept answering (no error), but neither the cliff flag of `RobotState` (always off) nor the raw cliff values
  (`cliff_data_raw` about 922, 0, 0, 0) changed: the effect of the threshold could not be observed at rest, and the default
  value is still not known. The robot has to be restarted afterwards.
- `FlashObjectIDs` (0x4d): sent 5 times, 3 s apart, with the object identifier of a connected cube (object 1): **nothing was
  seen on the cube** (watched by the operator). Tried again with the factory identifier of the cube (0xb85e9ee4) in the
  field, 3 messages 3 s apart after 3 with the object identifier: nothing was seen either. The message is not known to do
  anything on this robot; maybe the cube lights have to be in a given state, or the message is not handled by this firmware.
  A third attempt, on another cube (factory identifier 0xc6e6ecb8, again with both identifiers), after the cube lights were
  shown to work (`--tests cubelights`): nothing was seen either.
- `TurnInPlace` (0x39), relative turn of 90 degrees (`angle_rad` pi/2, 1 rad/s, `accel_rad_per_sec2` 3, tolerance 0.05, not
  absolute): with `num_half_revolutions` 0 the robot turned a quarter of a turn counterclockwise (odometry 89 degrees); with
  `num_half_revolutions` 1 it turned almost a full turn counterclockwise (odometry -21 degrees, that is 339 modulo 360, seen
  on the floor as nearly a full turn). The field adds turning to the angle: one unit is about a half revolution (the
  total, 339 degrees, is not exactly 90 + 180: not measured more precisely, the end of the turn is not checked here).

## Names from the C# code of the application

The APK holds the Unity code (`assets/bin/Data/Managed/Assembly-CSharp.dll`, managed code, so with the names of the
members, unlike the engine library). It declares a type for each message that the game layer uses. The fields of each
type, in order, were read from its metadata (a script with the `dnfile` module) and compared with the layout of the native
unpacking functions: a name was used only when the type of the C# (`ExternalInterface` or not) has the same name, the same
number of fields and compatible types. A third-party analysis of the same application (`cozmo-stack`) lists the same names.

Applied (names and, where it differed, the type):

| Number | Message | Fields |
|---|---|---|
| 0x07 | SetBodyRadioMode | `radio_mode`, `wifi_channel` |
| 0x3a | TurnInPlaceAtAngularSpeed | `speed_rad_per_sec`, `accel_rad_per_sec2` |
| 0x46 | StartControllerTestMode | `p1`, `p2`, `p3` (int32), `mode` |
| 0x47 | ControllerGains | `kp`, `ki`, `kd`, `max_integral_error`, `controller` |
| 0x4d | FlashObjectIDs | `object_id` |
| 0x51 | SetMotionModelParams | `slip_factor` |
| 0x53 | PowerState | `v_bat_fixed`, `v_ext_fixed`, `unused` (int32), `battery_level`, `on_charger`, `is_charging`, `operating_mode`, `charger_oos` |
| 0x59 | RollActionParams | `lift_height_mm`, `drive_speed_mmps`, `drive_accel_mmps2`, `drive_duration_ms`, `back_up_dist_mm` |
| 0x62 | ObjectConnectionStateToRobot | `object_id`, `factory_id`, `object_type`, `connected` |
| 0xbf | IMUDataChunk | `a_x`, `a_y`, `a_z`, `g_x`, `g_y`, `g_z` (8 **floats** each, not integers), `seq_id`, `chunk_id`, `total_num_chunks` |
| 0xc0 | CliffEvent | `timestamp`, `detected_flags`, `did_stop_for_cliff` |
| 0xc7 | IMURawDataChunk | the two arrays of 3 are `g` and `a`; the last two bytes are `order` and `timestamp` |
| 0xdc | IMUTemperature | `temperature_degc`, a **float** (the first declaration said uint32) |

The C# types of two messages did not match (`PlaceObjectOnGround` has 11 fields in the C#, 7 on the wire), they were not
used. Names that the catalog of a third-party analysis (`cozmo-stack`, from the strings of the engine, not checked against the
C#) gives for fields that PyCozmo already declared as `unknown*` were applied too; the wire format does not change, only the
keyword of the field (nothing in PyCozmo used the old ones):

| Number | PyCozmo | Field | Name |
|---|---|---|---|
| 0x39 | TurnInPlace | `unknown4`, `unknown5` (two bytes) | `num_half_revolutions` (uint16) |
| 0x3f | AppendPathSegPointTurn | `unknown` | `use_shortest_direction` |
| 0x45 | SetOrigin | `unknown0`, `unknown5` (uint32 `0x80000000`) | `timestamp`, `pose_angle_rad` (float, -0.0) |
| 0xc9 | HardwareInfo | `unknown1`, `unknown2` (two bytes) | `hw_version` (uint16) |
| 0xee | FirmwareSignature | `unknown` | `robot_id` |

The messages without a type in the C# keep `unknown*`: the game layer does not use them, so the names are only in the
native code, which does not keep them.

## Messages with arrays and strings

The 13 messages that were left out of the first declaration (the automatic extraction of the layouts did not follow
arrays and strings) were read in the unpacking functions of the original application. Their layouts are complete and
their sizes match the original where it is fixed.

Rule: an array or a string is a **length on 1 byte** followed by the elements (PyCozmo's default is 2 bytes, so the
declaration says it). A fixed array has no length.

| Number | Name | Layout |
|---|---|---|
| 0x0f | WriteBodyStorage | uint8, array of uint8 |
| 0xa0 | SetAppRunID | 4 uint32 (16 bytes) |
| 0xa1 | TestState | 4 uint32, 4 uint32, int16, 3 uint16, 3 uint16, 3 uint8 (49 bytes) |
| 0xa4 | BodyStorageContents | uint8, uint8, array of uint8 |
| 0xaa | AppConnectConfigString | uint8, 16 uint8 (17 bytes) |
| 0xb1 | PrintText | uint8, string |
| 0xb3 | GoalPose | 5 values of 4 bytes (a pose of the robot, 20 bytes), bool |
| 0xb7 | DataDump | uint32, array of uint8 |
| 0xbe | TimeProfileStat | uint32, int32, bool, string |
| 0xbf | IMUDataChunk | 6 arrays of 8 values of 4 bytes, 3 uint8 (195 bytes) |
| 0xc8 | DefaultCameraParams | float, float, uint16, int16, 17 uint8 (29 bytes) |
| 0xcf | CrashReport | uint32, int16, uint8, array of uint32 |
| 0xd2 | FWVersionInfo | uint32, int32, int32, 16 uint8, 16 uint8 (44 bytes) |

Assumed: the types of the values of `GoalPose` (4 floats and a uint32, as in the pose of `RobotState`) are not given by the
unpacking, only their size. The fields are named `unknown*` except
the data, the text and the name (and those of `IMUDataChunk`, see below).

On a robot (2026-11-23): `RequestCrashReports` (0x80, with 0) is answered by one `CrashReport` (0xcf) with all fields 0 and
an empty `dump`: the message and its 1-byte length are decoded; the robot has no crash to report. The other messages
(`PrintText`, `GoalPose`, `DataDump`, `TimeProfileStat`, `IMUDataChunk`, `DefaultCameraParams`, `FWVersionInfo`) were not received
during the tests and what triggers them is not known.

## Messages that only PyCozmo declares

PyCozmo declares messages that the original application list does not hold under the same numbers: the animation
keyframes and their control (`AnimHead`, `AnimLift`, `StartAnimation`, `EndAnimation`, `OutputAudio`...), the firmware
update, the state events (`RobotState`, `ImageChunk`, `ObjectAvailable`...) and a few more. They belong to other parts of
the protocol (the animation stream, the radio of the robot) and are not compared here.

## Open points

| Point | Status |
|---|---|
| Numbers, names and sizes of the 136 messages | Specified |
| Layout of the 51 messages that are now declared | Specified (order and size), types from the original |
| Meaning of the fields of the declared messages | Not validated |
| Messages with a variable size or an unrecovered layout (13) | Not modeled |
| The `factory` messages | Declared, never to be sent |
