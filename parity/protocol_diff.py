#!/usr/bin/env python3
"""
Differential check of the protocol layer: runs the same pseudo-random workload on a PyCozmo tree and prints one line
per case (result or exception type and message). Run it on master and on this branch, then compare the outputs:

    python3 parity/protocol_diff.py --root /path/to/master/checkout > master.txt
    python3 parity/protocol_diff.py --root . > branch.txt
    diff master.txt branch.txt

Cases: construction with random valid values (encoding, length, representation), decoding of random bytes for every
message, invalid values given to every field, and decoding of random and mutated frames.
"""

import argparse
import random
import re
import sys


def describe(fn):
    try:
        res = "ok " + str(fn())
    except Exception as e:      # noqa: BLE001 - the exception is the result being compared.
        res = "exc {}: {}".format(type(e).__name__, e)
    return re.sub(r" at 0x[0-9a-f]+", " at 0x...", res)


def random_value(rng, arg, decl, pe):
    k = type(arg).__name__
    ints = {"UInt8Argument": (0, 255), "UInt16Argument": (0, 65535), "UInt32Argument": (0, 4294967295),
            "Int8Argument": (-128, 127), "Int16Argument": (-32768, 32767),
            "Int32Argument": (-2147483648, 2147483647)}
    if k in ints:
        return rng.randint(*ints[k])
    if k == "FloatArgument":
        return rng.choice([0.0, 1.5, -2.25, rng.uniform(-1e6, 1e6), 1e30, 3])
    if k == "DoubleArgument":
        return rng.uniform(-1e9, 1e9)
    if k == "BoolArgument":
        return rng.choice([True, False, 0, 1, 2])
    if k == "EnumArgument":
        member = rng.choice(arg.enum_type.members)
        return rng.choice([getattr(pe, arg.enum_type.name)(member.value), member.value])
    if k == "FArrayArgument":
        vals = [random_value(rng, arg.data_type, decl, pe) for _ in range(arg.length)]
        if type(arg.data_type).__name__ == "UInt8Argument" and rng.random() < 0.5:
            return bytes(vals)
        return tuple(vals) if rng.random() < 0.5 else vals
    if k == "VArrayArgument":
        n = rng.randint(0, 12)
        vals = [random_value(rng, arg.data_type, decl, pe) for _ in range(n)]
        if type(arg.data_type).__name__ == "UInt8Argument" and rng.random() < 0.5:
            return bytes(vals)
        return vals
    if k == "StringArgument":
        return "".join(rng.choice("abcé€ xyz") for _ in range(rng.randint(0, 10)))
    if isinstance(arg, decl.Struct):
        cls = getattr(pe, arg.name)
        return cls(**{a.name: random_value(rng, a, decl, pe) for a in arg.arguments})
    raise NotImplementedError(k)


BAD_VALUES = [None, "x", "12", 3.7, -1, 256, 70000, 2 ** 40, -2 ** 40, float("nan"), float("inf"), (), (1, 2),
              b"\x01\x02", [1, 2, 3], object(), True]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="directory that contains the pycozmo package to test")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rounds", type=int, default=20)
    args = ap.parse_args()
    sys.path.insert(0, args.root)
    from pycozmo import protocol_declaration as decl, protocol_encoder as pe, frame

    rng = random.Random(args.seed)
    out = sys.stdout
    declared = decl.PROTOCOL.structs + decl.PROTOCOL.packets

    # Construction with random values.
    for _ in range(args.rounds):
        for s in declared:
            cls = getattr(pe, s.name)
            kwargs = {a.name: random_value(rng, a, decl, pe) for a in s.arguments}
            obj = describe(lambda: cls(**kwargs))
            out.write("new {} {}\n".format(s.name, obj))
            try:
                o = cls(**kwargs)
            except Exception:       # noqa: BLE001
                continue
            out.write("  bytes {}\n".format(describe(lambda: o.to_bytes().hex())))
            out.write("  len {}\n".format(describe(lambda: len(o))))
            out.write("  rt {}\n".format(describe(lambda: repr(cls.from_bytes(o.to_bytes())))))

    # Defaults.
    for s in declared:
        out.write("default {} {}\n".format(s.name, describe(lambda: repr(getattr(pe, s.name)()))))

    # Decoding random bytes.
    for _ in range(args.rounds):
        for s in declared:
            cls = getattr(pe, s.name)
            data = bytes(rng.randrange(256) for _ in range(rng.choice([0, 1, 3, 8, 20, 64, 200])))
            out.write("decode {} {} {}\n".format(s.name, data.hex(), describe(lambda: repr(cls.from_bytes(data)))))

    # Invalid values for every field.
    for s in declared:
        for a in s.arguments:
            for bad in BAD_VALUES:
                if isinstance(bad, object) and type(bad) is object:
                    label = "object"
                else:
                    label = repr(bad)
                out.write("set {}.{} {} {}\n".format(s.name, a.name, label, describe(
                    lambda: repr(getattr(pe, s.name)(**{a.name: bad})))))

    # Frames: random packets in engine and robot frames, mutated.
    ids = sorted(pe.PACKETS_BY_ID)
    for _ in range(args.rounds * 20):
        pkts = []
        for _ in range(rng.randint(0, 5)):
            pkt_cls = pe.PACKETS_BY_ID[rng.choice(ids)]
            sdecl = next(p for p in decl.PROTOCOL.packets if p.name == pkt_cls.__name__)
            try:
                pkts.append(pkt_cls(**{a.name: random_value(rng, a, decl, pe) for a in sdecl.arguments}))
            except Exception:       # noqa: BLE001
                pass
        ftype = rng.choice([decl.FrameType.ENGINE, decl.FrameType.ROBOT])
        first = rng.randint(0, 0xffff)
        seq = rng.choice([0xffff, first + sum(1 for p in pkts if not p.is_oob()) - 1, rng.randint(0, 0xffff)])
        raw = describe(lambda: frame.Frame(ftype, first, seq, rng.randint(0, 0xffff), pkts).to_bytes().hex())
        out.write("frame-enc {}\n".format(raw))
        if not raw.startswith("ok "):
            continue
        data = bytearray.fromhex(raw[3:])
        for _ in range(3):
            mutated = bytearray(data)
            if len(mutated) > 14 and rng.random() < 0.8:
                for _ in range(rng.randint(1, 4)):
                    mutated[rng.randrange(14, len(mutated))] = rng.randrange(256)
            if rng.random() < 0.3:
                mutated = mutated[:rng.randint(0, len(mutated))]

            def dec():
                f = frame.Frame.from_bytes(bytes(mutated))
                return "{} {} {} {} {}".format(f.type, f.first_seq, f.seq, f.ack,
                                               [(repr(p), p.seq, p.ack) for p in f.pkts])
            out.write("frame-dec {} {}\n".format(mutated.hex(), describe(dec)))


if __name__ == "__main__":
    main()
