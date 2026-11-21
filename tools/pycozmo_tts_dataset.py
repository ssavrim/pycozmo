#!/usr/bin/env python
"""

Build a speech dataset of Cozmo's voice from captures of the Cozmo app talking to the robot.

The audio streamed by the app is rebuilt from the captured packets, cut into utterances at pauses,
and written as WAVE files. With a file of the spoken sentences, one per line in the order they
were spoken, a metadata.csv file in LJSpeech format is written too.

See docs/tts_dataset.md.

"""

import argparse
import pathlib
import sys

from pycozmo import speech_capture


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("captures", nargs="+", type=pathlib.Path,
                        help="pcap or pcapng files, in the order they were recorded")
    parser.add_argument("-t", "--texts", type=pathlib.Path,
                        help="text file with the spoken sentences, one per line ('#' lines ignored)")
    parser.add_argument("-o", "--output", type=pathlib.Path, default=pathlib.Path("cozmo_tts_dataset"),
                        help="output directory (default: %(default)s)")
    parser.add_argument("-l", "--list", action="store_true",
                        help="only list the utterances found, without writing anything")
    parser.add_argument("--max-gap", type=float, default=0.5,
                        help="longest pause inside an utterance, in seconds (default: %(default)s)")
    parser.add_argument("--min-duration", type=float, default=0.3,
                        help="shortest utterance kept, in seconds (default: %(default)s)")
    parser.add_argument("--threshold", type=int, default=512,
                        help="peak amplitude of a voiced frame, 1 to 32768 (default: %(default)s)")
    parser.add_argument("--padding", type=float, default=0.1,
                        help="audio kept around each utterance, in seconds (default: %(default)s)")
    parser.add_argument("--prefix", default="cozmo", help="file name prefix (default: %(default)s)")
    parser.add_argument("-f", "--force", action="store_true", help="write into a non-empty output directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    utterances: list[speech_capture.Utterance] = []
    for capture in args.captures:
        frames = speech_capture.extract_audio_frames(speech_capture.read_pcap_payloads(capture))
        found = speech_capture.split_utterances(
            frames, max_gap=args.max_gap, min_duration=args.min_duration,
            threshold=args.threshold, padding=args.padding)
        print("{}: {} audio packets, {} utterances.".format(capture, len(frames), len(found)))
        utterances.extend(found)

    texts = speech_capture.read_texts(args.texts) if args.texts else None

    for i, utt in enumerate(utterances):
        text = texts[i] if texts is not None and i < len(texts) else ""
        print("{:4d}  {:10.3f}  {:6.2f} s  {}".format(i + 1, utt.start_time, utt.duration, text))

    if texts is not None and len(texts) != len(utterances):
        print("Error: {} sentences for {} utterances. Adjust --max-gap, --min-duration or --threshold, "
              "or the sentence file.".format(len(texts), len(utterances)), file=sys.stderr)
        return 1
    if args.list:
        return 0
    if not utterances:
        print("Error: no utterances found.", file=sys.stderr)
        return 1
    if args.output.exists() and any(args.output.iterdir()) and not args.force:
        print("Error: {} is not empty. Use --force to write into it.".format(args.output), file=sys.stderr)
        return 1

    speech_capture.write_dataset(utterances, args.output, texts=texts, prefix=args.prefix)
    total = sum(utt.duration for utt in utterances)
    print("Wrote {} utterances ({:.1f} min) to {}.".format(len(utterances), total / 60, args.output))
    if texts is None:
        print("No sentence file: only wavs/ and segments.csv were written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
