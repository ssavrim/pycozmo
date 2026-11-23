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
- `TurnInPlaceAtAngularSpeed` (0x3a): sent every 100 ms for 3 s with `speed_rad_per_sec` 0.5 and `unknown1` 0, the robot turned
  on the spot (about a quarter of a turn seen on the floor) and stopped after `stop_all_motors`. The odometry gave 70 degrees
  for 86 expected: the start-up ramp is not accounted for. The role of `unknown1` is not known (perhaps an acceleration).
- `MotorAutoEnabled` (0xd8): received twice while the robot was lifted, `motor_id` `MOTOR_LIFT`, `enabled` False. The
  motor identifier is decoded.
- `CliffEvent` (0xc0) and `RobotStopped` (0xd4): received while the robot drove at 20 mm/s and was lifted from
  the floor (not by lifting it at rest): `CliffEvent` x5 (`unknown0` 1650, `unknown1` 3, `unknown2` True) and `RobotStopped`
  x3 (`unknown0` 1). The meaning of the fields is not known (1650 looks like a sensor value). `PotentialCliff` was not received.

## Messages not declared

13 messages are still not declared, because their size varies or their layout was not recovered (arrays, strings,
samples of sensors):

| Number | Name | Direction | Size |
|---|---|---|---|
| 0x0f | WriteBodyStorage | engine to robot | variable |
| 0xa0 | SetAppRunID | engine to robot | 16 |
| 0xa1 | TestState | engine to robot | 49 |
| 0xa4 | BodyStorageContents | engine to robot | variable |
| 0xaa | AppConnectConfigString | engine to robot | 17 |
| 0xb1 | PrintText | robot to engine | variable |
| 0xb3 | GoalPose | robot to engine | variable |
| 0xb7 | DataDump | robot to engine | variable |
| 0xbe | TimeProfileStat | robot to engine | variable |
| 0xbf | IMUDataChunk | robot to engine | 195 |
| 0xc8 | DefaultCameraParams | robot to engine | 29 |
| 0xcf | CrashReport | robot to engine | variable |
| 0xd2 | FWVersionInfo | robot to engine | 44 |

A packet with one of these numbers received from the robot is not decoded.

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
