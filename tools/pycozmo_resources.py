#!/usr/bin/env python
"""

Cozmo resource manager.

Prepares the resources of the Cozmo Android application for PyCozmo, from a copy
of the application supplied by the user: an XAPK, or an Android application package with its OBB file,
or the OBB file alone. The reference version is 3.4.0 (version code 1204).

"""

import argparse
import pathlib
import shutil
import sys
from typing import Any

from pycozmo import resources


def progress_printer():
    last: dict[str, Any] = {"stage": None, "step": -1}

    def show(stage: str, fraction: float) -> None:
        step = int(fraction * 10)
        if stage != last["stage"] or step != last["step"]:
            last["stage"], last["step"] = stage, step
            print(f"  {stage}: {step * 10:3d}%", flush=True)

    return show


def dest_dir(args: argparse.Namespace) -> pathlib.Path:
    return pathlib.Path(args.dest) if args.dest else resources.resource_root()


def do_status(args: argparse.Namespace) -> int:
    """ Check whether resources are available. """
    root = dest_dir(args)
    if not (root / "resources.txt").exists():
        print(f"Resources NOT found in {root}")
        return 1
    print(f"Resources found in {root}")
    try:
        manifest = resources.load_manifest(root)
    except resources.ResourceError as e:
        print(f"  {e}")
        return 1
    if manifest is None:
        print("  No manifest: prepared by an older tool. Run 'prepare' again to record versions and checksums.")
        return 0
    print(f"  Version {manifest.version_name} ({manifest.version_code}), prepared {manifest.prepared_at}")
    for warning in manifest.warnings:
        print(f"  Warning: {warning}")
    return 0


def do_prepare(args: argparse.Namespace) -> int:
    """ Extract resources from the user's application package. """
    root = dest_dir(args)
    print(f"Preparing resources in {root}...")
    try:
        manifest = resources.prepare(
            args.source, obb=args.obb, dest=root, force=args.force, include_firmware=args.include_firmware,
            allow_other_version=args.allow_other_version, allow_other_signer=args.allow_other_signer,
            progress=progress_printer())
    except resources.ResourceError as e:
        print(f"ERROR: {e}")
        return 2
    for warning in manifest.warnings:
        print(f"Warning: {warning}")
    print(f"Resources of version {manifest.version_name} ({manifest.version_code}) prepared in {root}")
    return 0


def do_verify(args: argparse.Namespace) -> int:
    """ Check prepared resources against their manifest. """
    root = dest_dir(args)
    problems = resources.verify(root)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"Resources in {root} match their manifest.")
    return 0


def do_download(args: argparse.Namespace) -> int:
    """ Former automatic download: the resources now come from the user's copy of the application. """
    del args
    print("Automatic download has been removed: PyCozmo no longer fetches the Cozmo application from a "
          "third-party mirror.\n"
          "Obtain the Cozmo Android application 3.4.0 (XAPK, or application package and main.1204.com.anki.cozmo.obb), then run:\n"
          "  pycozmo_resources.py prepare <file> [--obb <obb file>]")
    return 1


def do_remove(args: argparse.Namespace) -> int:
    """ Remove resources. """
    root = dest_dir(args)
    if not (root / "resources.txt").exists():
        print(f"Resources NOT available in {root}")
        return 1
    shutil.rmtree(root)
    print(f"Resources successfully removed from {root}")
    return 0


def parse_args() -> argparse.Namespace:
    """ Parse command-line arguments. """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", help="resource directory (default: $PYCOZMO_DIR/assets)")
    subparsers = parser.add_subparsers(dest="cmd")
    subparsers.add_parser("status", help="show resource status")
    prepare = subparsers.add_parser("prepare", help="extract resources from the Cozmo application")
    prepare.add_argument("source", help="XAPK, application package or OBB file of the Cozmo application")
    prepare.add_argument("--obb", help="OBB file, when SOURCE is an application package")
    prepare.add_argument("--force", action="store_true", help="replace existing resources")
    prepare.add_argument("--include-firmware", action="store_true",
                         help="also extract robot firmware images (not needed by PyCozmo)")
    prepare.add_argument("--allow-other-version", action="store_true",
                         help="accept a version other than 3.4.0 (1204)")
    prepare.add_argument("--allow-other-signer", action="store_true",
                         help="accept an application package not signed by Anki")
    subparsers.add_parser("verify", help="check resources against their manifest")
    subparsers.add_parser("download", help="removed: use 'prepare'")
    subparsers.add_parser("remove", help="remove resources")
    args = parser.parse_args()
    if not args.cmd:
        print(parser.format_usage())
        sys.exit(1)
    return args


def main():
    args = parse_args()
    commands = {
        "status": do_status,
        "prepare": do_prepare,
        "verify": do_verify,
        "download": do_download,
        "remove": do_remove,
    }
    sys.exit(commands[args.cmd](args))


if __name__ == '__main__':
    main()
