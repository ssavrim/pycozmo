"""

Cozmo protocol frame representation and encoding and decoding.

The frame is implemented in Rust (crates/cozmo-core/src/frame.rs, crates/cozmo-py/src/frame.rs); this module
re-exports it.

"""

from ._cozmo import Frame


__all__ = [
    "Frame",
]
