
"""
The messages of the original application: names, numbers and sizes (docs/specs/protocol-gaps.md), compared with the
declaration of the protocol.
"""

import json
import os
import unittest

from pycozmo import protocol_declaration as declaration, protocol_encoder as pe


SIZES = {
    "FloatArgument": 4, "DoubleArgument": 8, "BoolArgument": 1, "UInt8Argument": 1, "UInt16Argument": 2,
    "UInt32Argument": 4, "Int8Argument": 1, "Int16Argument": 2, "Int32Argument": 4,
}

#: Messages whose declared size differs from the one of the original application, and why.
KNOWN_DIFFERENCES = {
    0xa9: "ShutdownRobot: the original has a payload of 32 bytes that PyCozmo does not declare",
}


def argument_size(argument):
    name = type(argument).__name__
    if name in SIZES:
        return SIZES[name]
    if name == "EnumArgument":
        return argument_size(argument.data_type)
    if name == "FArrayArgument":
        return argument.length * argument_size(argument.data_type)
    if name == "VArrayArgument" or name == "StringArgument":
        return None
    if hasattr(argument, "arguments"):
        sizes = [argument_size(a) for a in argument.arguments]
        return None if None in sizes else sum(sizes)
    return None


def packet_size(packet):
    sizes = [argument_size(a) for a in packet.arguments]
    return None if None in sizes else sum(sizes)


def load_reference():
    with open(os.path.join(os.path.dirname(__file__), "protocol_reference.json")) as f:
        return {int(k, 16): v for k, v in json.load(f).items()}


def declared():
    return {p.id: p for p in declaration.PROTOCOL.packets if isinstance(p.id, int)}


class TestProtocolReference(unittest.TestCase):

    def test_reference_is_complete(self):
        reference = load_reference()
        self.assertEqual(len(reference), 136)
        self.assertEqual(sum(1 for v in reference.values() if v["side"] == "engine to robot"), 82)
        self.assertEqual(sum(1 for v in reference.values() if v["side"] == "robot to engine"), 54)

    def test_sizes(self):
        reference = load_reference()
        packets = declared()
        compared = 0
        for number, entry in reference.items():
            if number not in packets or entry["size"] is None:
                continue
            size = packet_size(packets[number])
            if size is None or number in KNOWN_DIFFERENCES:
                continue
            compared += 1
            self.assertEqual(
                size, entry["size"], "0x{:02x} {} (PyCozmo: {})".format(number, entry["name"], packets[number].name))
        self.assertGreater(compared, 100)

    def test_known_differences_still_differ(self):
        reference = load_reference()
        packets = declared()
        for number in KNOWN_DIFFERENCES:
            self.assertNotEqual(packet_size(packets[number]), reference[number]["size"], hex(number))

    def test_declared_messages_of_the_original(self):
        """ The messages added from the original application are declared with the name of the original. """
        packets = declared()
        expected = ((0x3a, "TurnInPlaceAtAngularSpeed"), (0x4a, "IMURequest"), (0x54, "SetCliffDetectThreshold"),
                    (0x63, "EnableBraceWhenFalling"), (0xc0, "CliffEvent"), (0xc7, "IMURawDataChunk"),
                    (0xd8, "MotorAutoEnabled"))
        for number, name in expected:
            self.assertEqual(packets[number].name, name)

    def test_names_that_differ_are_known(self):
        """ PyCozmo keeps its own name for some messages; the differences are listed in the specification. """
        reference = load_reference()
        packets = declared()
        different = {n for n, e in reference.items()
                     if n in packets and packets[n].name.lower() != e["name"].lower()}
        with open(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "specs", "protocol-gaps.md")) as f:
            text = f.read()
        for number in sorted(different):
            self.assertIn("0x{:02x}".format(number), text, "0x{:02x} is not in the specification".format(number))

    def test_all_messages_are_declared(self):
        reference = load_reference()
        packets = declared()
        self.assertEqual(sorted(set(reference) - set(packets)), [])

    def test_arrays_and_strings(self):
        """ The arrays and strings of the messages added from the original application have a length on 1 byte. """
        text = pe.PrintText(unknown0=3, text="hello")
        self.assertEqual(text.to_bytes(), b"\x03\x05hello")
        self.assertEqual(pe.PrintText.from_bytes(text.to_bytes()).text, "hello")
        crash = pe.CrashReport(unknown0=1, unknown1=-2, unknown2=3, dump=(1, 2, 3))
        self.assertEqual(len(crash.to_bytes()), 4 + 2 + 1 + 1 + 12)
        self.assertEqual(list(pe.CrashReport.from_bytes(crash.to_bytes()).dump), [1, 2, 3])
        dump = pe.DataDump(unknown0=7, data=(9, 8))
        self.assertEqual(dump.to_bytes(), b"\x07\x00\x00\x00\x02\x09\x08")
        stat = pe.TimeProfileStat(unknown0=1, unknown1=-1, unknown2=True, name="x")
        self.assertEqual(pe.TimeProfileStat.from_bytes(stat.to_bytes()).name, "x")
        version = pe.FWVersionInfo(unknown0=1, unknown1=2, unknown2=3, unknown3=tuple(range(16)),
                                   unknown4=tuple(range(16, 32)))
        self.assertEqual(len(version.to_bytes()), 44)
        self.assertEqual(len(pe.IMUDataChunk(a_x=(0.0,) * 8, a_y=(0.0,) * 8, a_z=(0.0,) * 8,
                                             g_x=(0.0,) * 8, g_y=(0.0,) * 8, g_z=(0.0,) * 8,
                                             seq_id=0, chunk_id=0, total_num_chunks=0).to_bytes()), 195)
