
Contributing to PyCozmo
=======================

Any contribution to PyCozmo is welcome and appreciated.


Bug Reports and Feature Requests
--------------------------------

- Bug reports and feature requests should be made using [GitHub issues](https://github.com/zayfod/pycozmo/issues).


Pull Requests
-------------

- Pull requests should be made against the [dev branch](https://github.com/zayfod/pycozmo/tree/dev).
- All [tests](https://github.com/zayfod/pycozmo/actions) should be passing on a PR, before it can be merged.
- Code should adhere to the [PEP 8 style guide](https://www.python.org/dev/peps/pep-0008/).
- Using docstrings is encouraged.
- Adding tests is encouraged. 


Development setup
-----------------

Install [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer, then run:

```bash
uv sync --extra vision
uv run --extra vision pytest
uv run --extra vision flake8 . --count --statistics
uv run --extra vision mypy --strict --follow-imports=skip pycozmo/companion.py
```

The vision extra installs OpenCV and enables the face and motion tests. The
remaining tests run without it. The project's historical codebase still has
existing mypy findings, so CI currently enforces the type check on the new
companion policy instead of treating the entire legacy codebase as type-clean.
See `mypy.ini` for the configuration. To build distributions, run `uv build`.
Build the documentation with:

```bash
uv run --group docs sphinx-build -b html -W docs/source /tmp/pycozmo-docs
```

Robot tests and proprietary resources are not required for the test suite. If
checking on hardware, describe the firmware, the physical test and its result
in the pull request; do not commit extracted application assets or personal
face samples.
