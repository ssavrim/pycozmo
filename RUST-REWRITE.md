# PyCozmo in Rust: an empirical study

This branch (`dev/rust-rewrite`, an orphan branch) rewrites the performance- and correctness-critical core of
PyCozmo in Rust and keeps the public Python API (`import pycozmo`) through a PyO3 extension. The original Python
test suite is the parity suite. Everything here was tested **offline**; nothing was run on a robot.

**Short version.** The protocol (all 156 messages, generated from `protocol_declaration.py`), the frame layer, the
sliding windows, the display image codec, the animation FlatBuffers codec, the procedural face renderer and the
emotion decay are in Rust. The full suite gives the same result on master and on the branch, test by test
(531 passed, 5 skipped, 0 failed out of 536), with 2 test files edited for wording only (see
[Edited tests](#edited-tests)). Additional differential checks against master (about 40,000 cases) found identical
results on valid input; the differences are on invalid input and are listed. Speed-ups range from 1.9x (face
rendering, which Pillow already did in C) to 69x (mood decay). The connection threads, the client API and the
application layers are still the original Python, running on top of the Rust core.

## Contents

1. [Architecture](#architecture)
2. [Building and testing](#building-and-testing)
3. [Module status](#module-status)
4. [Parity results](#parity-results)
5. [Differential checks beyond the suite](#differential-checks-beyond-the-suite)
6. [Known behavioural differences](#known-behavioural-differences)
7. [Benchmarks](#benchmarks)
8. [Lines of code](#lines-of-code)
9. [Type safety and stability](#type-safety-and-stability)
10. [Effort and estimate to finish](#effort-and-estimate-to-finish)
11. [Risks and what needs the robot](#risks-and-what-needs-the-robot)
12. [Clean-room notes](#clean-room-notes)
13. [What was not done](#what-was-not-done)

## Architecture

```
pycozmo/                      Python package (same module names and API as PyCozmo)
  protocol_encoder.py         shim: re-exports the generated Rust classes      ┐
  protocol_base.py, frame.py  shims                                            │
  window.py                   shim                                             │  pycozmo._cozmo
  image_encoder.py            shim + Python text helpers                       │  (PyO3 extension,
  emotions.py                 DecayGraph/EmotionType from Rust, rest Python    │   crates/cozmo-py)
  anim_encoder.py             classes Python, .bin read/write in Rust          │        │
  procedural_face.py          classes Python, ProceduralFace.render() in Rust  ┘        │
  conn.py, client.py, ...     unchanged Python, using the classes above                 ▼
                                                                               crates/cozmo-core
crates/cozmo-core             pure Rust, no Python dependency                  (protocol, frames,
crates/cozmo-py               PyO3 0.25 binding, cdylib "pycozmo._cozmo"       windows, codecs,
codegen/gen_rust_protocol.py  generator of the protocol code (core + binding)  face, emotions)
parity/*.py                   differential checks against another PyCozmo tree
bench/compare.py              benchmark of identical workloads on two trees
```

* **Cargo workspace** with two crates. `cozmo-core` holds the logic and its own unit tests; it does not know about
  Python. `cozmo-py` converts between Python objects and core types and maps core errors to the Python exceptions
  that PyCozmo raises for the same conditions (`ValueError`, `IndexError`, `struct.error`, `AssertionError`...).
* **Protocol generated, not hand written.** `codegen/gen_rust_protocol.py` reads `pycozmo/protocol_declaration.py`
  (the same source as `protocol_generator.py`) and writes `cozmo-core/src/protocol/messages.rs` (typed structs and
  enums, encoding, decoding, lengths, identifier and group tables, and a round-trip test of every message) and
  `cozmo-py/src/protocol_gen.rs` (one PyO3 class per message), plus the Python shim `protocol_encoder.py`.
  Changing the declaration and re-running the generator updates all three.
* **Where the data lives.** A packet object is a PyO3 class hierarchy `Struct → Packet → <Message>`. The `Packet`
  base holds the core `Packet` (type, id, seq, ack and the typed `Message`), so frames are encoded and decoded
  entirely in Rust, without calling back into Python per packet. Message subclasses only add properties.
* **Python semantics kept where tests or callers can see them**: constructor arguments (positional and keyword,
  explicit `None` validated, not defaulted), the validation rules and error messages of `protocol_utils.py`,
  values returned as `bytes` for decoded byte arrays and as lists otherwise, Python `enum.Enum` classes for the
  protocol enumerations (created at import with the functional API, module `pycozmo.protocol_encoder`), identity of
  the `LightState` objects stored in a packet (the client tests compare them with `is`-equality), and the order in
  which errors occur (all fields are read before enumerations are converted, as in the Python constructor).
* **Why not a different architecture.** Two alternatives were considered: (a) keep the generated Python classes and
  only move `BinaryReader`/`BinaryWriter` to Rust; this keeps per-field Python overhead and makes little use of
  Rust; (b) move the connection threads to Rust as well. (b) would put UDP timing and retransmission, which can
  only be validated on the robot, behind the FFI in a first step; it was left for a later step (see the estimate).
  For animations and faces the Python classes are the data model that the rest of PyCozmo manipulates, so only the
  bulk operations moved to Rust (FlatBuffers read/write of whole files, rendering of a whole face).

## Building and testing

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install maturin==1.16.* pytest dpkt numpy "Pillow>=9.1" "flatbuffers>=2.0" \
    "opencv-contrib-python-headless>=4.12,<6" "mcp>=2,<3"
maturin develop --release           # builds pycozmo/_cozmo*.so and installs the package in editable mode
python -m pytest pycozmo            # the parity suite
cargo test                          # Rust unit tests
cargo clippy --all-targets -- -D warnings
python codegen/gen_rust_protocol.py # after changing pycozmo/protocol_declaration.py
```

Toolchain used: Rust 1.97, PyO3 0.25.1 (pinned), maturin 1.16, CPython 3.13.18, Pillow 13.0.0, NumPy 2.5.5,
on Linux x86_64 (2 vCPU Intel Xeon @ 2.10 GHz).

## Module status

| Module | Status | Notes |
|---|---|---|
| `protocol_encoder` | **Rust** (generated), Python shim | 16 enums, 2 structures, 156 messages |
| `protocol_base` | **Rust**, Python shim | `Struct`, `Packet`, `UnknownPacket`, `UnknownCommand`, `UnknownEvent` |
| `frame` | **Rust**, Python shim | encoding, decoding, skipped packets, sequence checks |
| `window` | **Rust**, Python shim | `BaseWindow`, `ReceiveWindow`, `SendWindow` |
| `image_encoder` | **Rust** (`ImageEncoder`, `ImageDecoder`) + Python text helpers | `render`, `image_to_str`, `str_to_image` stay Python |
| `emotions` | **Rust** (`DecayGraph`, `EmotionType`) + Python | `Node`, `EmotionEvent`, `Mood`, loaders stay Python |
| `anim_encoder` | Python classes, **Rust** FlatBuffers read/write | `AnimClips.from_fb_stream/_file`, `to_fb_stream/_file` |
| `procedural_face` | Python classes, **Rust** `ProceduralFace.render()` | `ProceduralEye.render()`, `ProceduralLid.render()` stay Python (Pillow) |
| `protocol_ast`, `protocol_declaration`, `protocol_generator`, `protocol_utils` | unchanged Python | declaration and the Python generator are the sources of the Rust code; `protocol_utils.BinaryReader` is used by `client.py` |
| `conn` | unchanged Python | threads and sockets; uses the Rust frames, windows and packets |
| `client`, `robot`, `camera`, `sensors`, `lights`, `cube_lights`, `object`, `filter`, `event`, `run`, `logger`, `exception` | unchanged Python | |
| `anim`, `anim_controller`, `anim_library`, `anim_audio`, `audio`, `audiokinetic/*` | unchanged Python | use the Rust packets, image encoder and face renderer |
| `brain`, `activity`, `behavior`, `needs`, `companion`, `edges`, `face_registry`, `vision`, `markers`, `expressions` | unchanged Python | `activity` and `needs` use the Rust `DecayGraph` |
| `util` | unchanged Python | geometry value classes; see [estimate](#effort-and-estimate-to-finish) |
| `resources`, `markers` | Python, wording paraphrased | see [Clean-room notes](#clean-room-notes) |
| `mcp_server`, `robot_debug`, `json_loader`, `CozmoAnim/*`, `tools/*`, `examples/*` | unchanged Python | glue, tables, generated FlatBuffers accessors, scripts |

## Parity results

The suite is `pycozmo/tests` and `pycozmo/audiokinetic/tests` (536 tests), copied from master `00e5d33`. Master and
the branch were run with the same interpreter (CPython 3.13.18) in two virtual environments with the same package
versions, on the same machine.

| Step (commit) | Passed | Failed | Skipped |
|---|---:|---:|---:|
| master `00e5d33` (baseline) | 531 | 0 | 5 |
| Python sources imported, wording paraphrased (`ab06df2`) | 531 | 0 | 5 |
| Protocol, packets and frames in Rust (`b01476b`) | 531 | 0 | 5 |
| Exact constructor arguments and decoding order (`c59f885`) | 531 | 0 | 5 |
| Windows and image codec in Rust (`fc18551`) | 531 | 0 | 5 |
| Decay graph and emotions in Rust (`567b900`) | 531 | 0 | 5 |
| Animation FlatBuffers codec in Rust (`bba683a`) | 531 | 0 | 5 |
| Procedural face rendering in Rust (`218f5f3`, `7b014fb`) | 531 | 0 | 5 |
| Final | 531 | 0 | 5 |

The outcome of every test, compared by test identifier, is the same on master and on the branch.

During the protocol step, three tests failed before the identity of `LightState` objects stored in packets was kept
(`test_backpack_lights.py` x2, `test_cube_api.py`): PyCozmo stores the objects passed to `LightStateCenter`,
`LightStateSide` and `CubeLights`, and the tests compare them by identity. The binding now keeps those objects and
copies their current values when the packet is encoded. No test was changed for this.

**Skipped on both** (environment, not the port): `test_conn.py:55` (marked intermittently failing in PyCozmo),
`test_original_resources.py` x3 (need the original resources and `vgmstream-cli`), `test_speech.py:67` (needs
eSpeak NG). Note that running the suite with the system Python of this machine instead of the virtual environment
fails 3 `test_vision.py` tests on master too (the OpenCV package there lacks its cascade data); that is why both
runs use dedicated environments.

**What the suite covers of the Rust code**: `test_frame.py`, `test_protocol_reference.py`,
`test_docking_messages.py`, `test_anim_protocol.py`, `test_body_color.py`, `test_regressions.py` and the client
tests (packets and frames), `test_conn.py` (real UDP on the loopback interface between a client and a server
connection, through the Rust frames and windows), `test_window.py`, `test_image_encoder.py`,
`test_image_decoder.py`, `test_anim_encoder.py`, `test_anim_library.py`, `test_decay_graph.py`,
`test_mood_needs.py`. The suite has no direct test of the face renderer (only `_draw_shape`, which stays Python),
which is why the differential checks below were added.

### Edited tests

No test was edited because of a private implementation detail. Two test files were edited **only** for the
clean-room wording rule (they contained a forbidden word); no assertion was removed or weakened, each assertion
still checks the same condition with the new wording of the message it tests:

| File | Edit | Reason |
|---|---|---|
| `pycozmo/tests/test_markers.py` (`TestPrepare`) | fake source attribute and helper renamed `app_package`; asserted messages `"needs the application package"`, `"is not in the application package"` | wording rule; the messages of `resources.py` were paraphrased the same way |
| `pycozmo/tests/test_resources.py` | member name `com.anki.cozmo.package` in the synthetic XAPK, file `cozmo.package` and variable `package` | wording rule; `resources.py` now finds the application package inside an XAPK by its content (an archive with `AndroidManifest.xml`) instead of by its file extension |

Rust tests: 29 unit tests in `cozmo-core` (wire format, frames including the frames of the Python tests, unknown
packets, windows, image codec, decay graph, emotions, animation codec with malformed input, face rendering helpers,
and a generated round trip of every declared message). `cargo clippy --all-targets -- -D warnings` is clean.

## Differential checks beyond the suite

Each script runs the same seeded workload on a PyCozmo tree given with `--root` and prints one line per case; the
outputs of master and of the branch are compared with `diff`.

| Script | Cases | Result |
|---|---:|---|
| `parity/protocol_diff.py` | 24,800 lines: random valid construction of every structure and message (bytes, length, repr, round trip), defaults, decoding of random bytes for every message, 17 invalid values for every field, random frames encoded then mutated or truncated and decoded | identical except 52 lines, all on invalid input: 46 for non-`str` values given to a string field or `bytes` given to an array of structures, 6 for invalid UTF-8 (see below) |
| `parity/image_diff.py` | 5,000 lines: 500 images (noise, rectangles, ellipses, text) encoded and decoded, 2,000 random byte sequences decoded (decoder state and exceptions) | identical, including the 95 images that PyCozmo's encoder does not encode losslessly (35 decode to another image, 60 fail to decode) and the exceptions on malformed sequences |
| `parity/anim_diff.py` | 200 random sets of clips with every keyframe type: encoded bytes, decoded clips (as JSON), 600 corrupted or truncated buffers | encoded bytes **identical** to `flatbuffers.Builder`, decoded clips identical; corrupted data raises `ValueError` where PyCozmo raises `struct.error`, `TypeError`, `UnicodeDecodeError`, `AttributeError` or `AssertionError` |
| `parity/face_diff.py --count 6000` | 6,001 faces (default, realistic, extreme, random), SHA-1 of the pixels | **pixel-identical** on all 6,001 (3,787 non-blank) |

A robustness run also fed 200,000 random buffers each to `Frame.from_bytes`, `ImageDecoder.decode` and
`AnimClips.from_fb_stream` (600,000 calls): only ordinary Python exceptions, no Rust panic.

## Known behavioural differences

All on invalid input or in debug output; none is visible to the suite.

1. A string field given a non-`str` value raises `TypeError` when it is set; PyCozmo stores the value and fails when
   encoding (or encodes a sequence).
2. An array of structures given `bytes` raises `ValueError` when it is set; PyCozmo stores it and fails when encoding.
3. Invalid UTF-8 in a decoded string raises `ValueError` (with another message) instead of `UnicodeDecodeError`
   (a subclass of `ValueError`; frames skip the packet in both cases).
4. Setting an enumeration field to an object whose `.value` is valid for the wire type but is not a member of the
   enumeration raises `ValueError`; PyCozmo stores the object as is.
5. Corrupted animation files raise `ValueError` instead of the various exceptions of the Python FlatBuffers reader.
6. Packets in a frame must be instances of the classes of `pycozmo.protocol_encoder`/`protocol_base` (or their
   subclasses); PyCozmo also accepted any object with the right attributes and `to_writer()`.
7. Reading a list or bytes field returns a new object each time (PyCozmo returned the stored list, so in-place changes
   to it changed the packet). No caller in PyCozmo relies on it. The arrays of `LightState` are the exception: they
   keep the stored objects, as above.
8. `DecayGraph.evaluate()` returns a Python `float` instead of a `numpy.float64`; unsorted node abscissas give a
   different (undefined in NumPy too) result.
9. `ImageDecoder` with `debug = True` prints one line per message instead of PyCozmo's layout.
10. `ProceduralFace.render()` takes the eye and lid offsets from the Python objects but not changes of their width or
    height made after construction.

## Benchmarks

`bench/compare.py compare` runs each workload in a fresh process per tree; each sample lasts at least 0.1 s, 7
samples, median time per operation. Baseline: master in its virtual environment; candidate: this branch built with
`maturin develop --release`. Same machine (2 vCPU Xeon @ 2.10 GHz), same CPython 3.13.18. Raw results:
`bench/results/compare.json`.

| Workload | master (median per op) | branch (median per op) | Speed-up |
|---|---:|---:|---:|
| protocol: construct 5 messages | 1.69 us | 387 ns | 4.4x |
| protocol: encode 10 messages | 2.34 us | 232 ns | 10.1x |
| protocol: decode 10 messages | 4.82 us | 493 ns | 9.8x |
| frame: pack 5 packets | 12.6 us | 1.42 us | 8.8x |
| frame: unpack robot frame (3 packets) | 32.6 us | 1.61 us | 20.2x |
| window: 60 packets through send/receive windows | 35 us | 7.11 us | 4.9x |
| image: encode 20 display images | 465 us | 20.1 us | 23.2x |
| image: decode 20 display images | 172 us | 3.94 us | 43.7x |
| anim: encode 20 clips (FlatBuffers) | 65.5 ms | 7.26 ms | 9.0x |
| anim: decode 20 clips (FlatBuffers) | 175 ms | 6.94 ms | 25.2x |
| procedural face: render 10 faces | 294 us | 158 us | 1.9x |
| mood: decay of 9 emotions | 51.5 us | 747 ns | 68.9x |
| decay graph: 5 evaluations | 1.25 us | 54.9 ns | 22.7x |

Notes. "Per op" is per message, per image, per face or per call as named. Construction stays dominated by the
creation of Python objects and argument parsing. Animation decoding still creates the Python keyframe objects; the
FlatBuffers work itself is a small part of the 6.9 ms. Face rendering was already C (Pillow); the Rust renderer does
the same pixel work and gains mostly on the Python glue. `import pycozmo` takes about the same time (131 to 145 ms
on the branch, 148 to 162 ms on master). The test suite takes 4.4 s on the branch and 4.5 to 5.0 s on master; it is
dominated by sleeps and thread timeouts, so it is not a performance measure. The extension is 4.9 MB (release,
unstripped).

## Lines of code

| Area | Python on master | Branch: Rust | Branch: Python |
|---|---:|---:|---:|
| Protocol messages | 13,213 (generated) | 9,495 core (generated) + 11,405 binding (generated) | 195 (generated shim) |
| Protocol generator | 576 (`protocol_generator.py`, kept) | 667 (`codegen/gen_rust_protocol.py`, Python) | 576 (kept) |
| Packets, wire format, errors | 180 + 318 | 249 + 235 + 61 core, 330 + 301 + 91 + 34 binding | 18 |
| Frames | 175 | 245 core + 85 binding | 15 |
| Windows | 173 | 240 core + 182 binding | 17 |
| Image codec | 288 | 377 core + 113 binding | 65 |
| Emotions (decay, values) | 261 | 170 core + 186 binding | 184 (rest of module) |
| Animation FlatBuffers | 1,116 (module) | 863 core + 265 binding | 1,123 (module) |
| Procedural face | 608 (module) | 935 core + 24 binding | 587 (module) |
| Parity scripts, benchmark | – | – | 403 + 305 |

Hand-written Rust: about 3,400 lines in the core (of which about 400 lines of tests) and 1,700 in the binding.
Generated Rust: about 20,900 lines. Python library code (tests excluded): 32,500 lines on master, of which 13,200
generated; 18,700 lines on the branch, almost all of it unchanged.

## Type safety and stability

* **No `unsafe`**: both crates declare `#![forbid(unsafe_code)]`. PyO3 uses `unsafe` internally.
* **Typed core**: messages are structs with fixed-width integer fields and Rust enums; an out-of-range value cannot
  be stored in the core, so range errors are reported where a Python value is converted, with PyCozmo's messages.
  Fields declared as 32-bit floats are stored as `f64`, because PyCozmo keeps the Python float until encoding
  (reading a field back gives the value that was set); the conversion to 32 bits, with PyCozmo's overflow error,
  happens at encoding.
* **Panics**: a Rust panic reaches Python as `pyo3_runtime.PanicException`, a `BaseException`. The `except Exception`
  blocks of the connection threads would not catch it, so the code avoids panics: outside tests the only panicking
  calls are 5 `expect()` on the enumeration registry, which is filled when the module is imported. Buffer access is
  checked (`Reader`, the FlatBuffers reader and the image decoder return errors). The 600,000-call fuzz run above
  produced no panic. Python's own failure modes are kept: `IndexError`, `AssertionError`, `UnboundLocalError` and
  `NotImplementedError` are raised in the same situations as by PyCozmo (frames, image decoder).
* **Clippy**: clean with `-D warnings` on the hand-written code. The generated modules opt out of the `clippy::all`
  group: without the opt-out they give 582 style warnings (needless `let` before return, redundant closures,
  unneeded parentheses, no-op arithmetic in length sums), none of the correctness group.
* **GIL**: the face renderer releases the GIL (`allow_threads`); the other calls are short and keep it.

## Effort and estimate to finish

Effort actually spent, as wall-clock time of the agent session (one agent, no robot): exploration of the code and
tests, environments and baseline about 45 min (before an interruption); then, from the commit times: import and
clean-room wording 10 min, protocol code generator, core, binding and frames 17 min, exact argument parsing and the
protocol differential check 5 min, windows and image codec 5 min, emotions 4 min, animation FlatBuffers codec 5 min,
procedural face 7 min plus 5 min of optimisation, benchmark and report about 20 min. Total about 2 h. This is the
speed of an agent that reads Pillow's and FlatBuffers' sources and generates code; for a developer, multiply by a
large factor (our estimate: 6 to 10 days of work for the same result).

Estimate to finish (developer days, including differential checks like the ones above; robot sessions extra):

| Remaining area | Python lines | Estimate | Notes |
|---|---:|---:|---|
| `conn` (threads, sockets, retransmission) | 512 | 3 to 5 d | sans-IO state machine in Rust plus a thread or async runtime; needs the robot |
| `client`, `robot`, `camera`, `sensors`, `lights`, `cube_lights`, `object`, `filter`, `event` | 2,100 | 5 to 8 d | callbacks to Python handlers; camera needs the robot |
| `anim`, `anim_controller`, `anim_library`, `anim_audio`, `audio`, `audiokinetic` | 2,600 | 5 to 8 d | animation timing and audio sync need the robot |
| `util` geometry | 900 | 1 to 2 d | value classes; little speed gain because Python call overhead dominates |
| `brain`, `activity`, `behavior`, `needs`, `companion`, `edges`, `face_registry`, `expressions` | 3,300 | 6 to 10 d | mostly decision logic with Python callbacks |
| `vision`, `markers` | 760 | 3 to 5 d, or keep Python | depend on OpenCV |
| `resources`, `robot_debug`, `mcp_server`, tools | 3,600 | keep Python | glue, tables, protocol for AI clients |

## Risks and what needs the robot

**Tested offline**: byte-exact message and frame encoding and decoding, window arithmetic, the image codec, the
FlatBuffers output, face pixels, decay values, the UDP exchange between a client and a server connection on the
loopback interface (`test_conn.py`), and the client logic of the suite with patched connections.

**Needs verification on the robot** (not done here):

* **UDP timing and retransmission**: the send/receive threads are still Python and now call Rust for frames and
  windows; per-frame work is 9x to 20x shorter, which changes the timing of the send thread (collect interval,
  acknowledgement timeout). Not measured with a real link.
* **Camera stream**: `ImageChunk` decoding and frame reassembly at the robot's rate; the chunk decoding is Rust, the
  JPEG assembly is Python.
* **Animation sync**: audio frames, keyframes and `AnimationState` feedback; the packets are Rust, the controller and
  its timing are Python.
* **Display**: images produced by the Rust encoder are identical to PyCozmo's, so the risk is low, but the screen was
  not checked.
* **Protocol version**: the Rust code is generated from the same declaration (firmware 2381); behaviour with other
  firmware versions was not checked.
* **Platforms**: built and tested only on Linux x86_64 with CPython 3.13. Wheels for other platforms (and the ARM
  boards often used with Cozmo) were not built; the extension is built for one CPython version (`abi3` was not used).
* **Panics in threads**: a panic would surface as a `BaseException`; see above.
* **Two sources of truth**: the generated Python encoder of master is replaced by the Rust one; `protocol_generator.py`
  still exists and can regenerate a Python `protocol_encoder.py` that would overwrite the shim.

## Clean-room notes

The branch must not contain some words. The copied sources contained them in passages about the user's
application package; these passages were paraphrased in our own words, without changing the behaviour:

* `pycozmo/resources.py`: identifiers, messages and comments about the application package; an XAPK member is now
  recognised by its content (an archive with `AndroidManifest.xml`) instead of by its extension.
* `pycozmo/markers.py`: the location of the engine library is given as a pattern (`lib/armeabi-v7a/*Engine.so`),
  matched with `fnmatch` by `resources.py`, with a paraphrased comment.
* `pycozmo/tests/test_markers.py`, `pycozmo/tests/test_resources.py`: see [Edited tests](#edited-tests).
* `tools/pycozmo_resources.py`, `tools/pycozmo_validate.py`: help and messages.
* `docs/specs/protocol-gaps.md` (needed by `test_protocol_reference.py`): one paragraph about the names of the
  messages. The other documents of master, `README.md` and `CHANGES.md` were not copied to the branch.

## What was not done

* The connection threads, the client API and the application layers (see the estimate) are still Python.
* No robot validation (by design of the study).
* No wheels for other platforms, no `abi3` build, no CI configuration.
* The suite has no direct test of the face renderer and of the animation codec's byte output against
  `flatbuffers.Builder`; both are covered by the differential scripts, which need a master checkout to run.
* The `util` geometry classes were not ported: they are small value objects for which the cost of a call through
  the binding is similar to the Python code itself.
