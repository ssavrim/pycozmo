# PyCozmo, Rust core (study branch)

This orphan branch is a study: the protocol, frame layer, sliding windows, display image codec, animation
FlatBuffers codec, procedural face renderer and emotion decay of [PyCozmo](https://github.com/zayfod/pycozmo) are
implemented in Rust (`crates/`), behind the same Python API (`import pycozmo`). The original test suite gives the
same results as on master (two test files were edited for wording only). Nothing was validated on a robot.

Read [RUST-REWRITE.md](RUST-REWRITE.md) for the architecture, the parity and benchmark results, and what remains.

```sh
pip install maturin==1.16.* pytest dpkt numpy "Pillow>=9.1" "flatbuffers>=2.0" "opencv-contrib-python-headless>=4.12,<6"
maturin develop --release
python -m pytest pycozmo
cargo test
```

License: MIT, see [LICENSE.md](LICENSE.md) and [NOTICE](NOTICE).
