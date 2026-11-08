#!/usr/bin/env python
"""Speak text through Cozmo using an independently installed eSpeak NG."""
import argparse
import threading

import pycozmo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('text', help='Text to synthesize')
    parser.add_argument('--voice', default='en', help='eSpeak NG voice, for example fr')
    parser.add_argument('--executable', default='espeak-ng', help='Path to eSpeak NG executable')
    args = parser.parse_args()

    with pycozmo.connect() as cli:
        completed = threading.Event()
        handler = cli.add_handler(pycozmo.event.EvtAudioCompleted, lambda _cli: completed.set())
        try:
            cli.say_text(args.text, voice=args.voice, executable=args.executable)
            if not completed.wait(65):
                raise TimeoutError('Audio playback did not complete')
        finally:
            cli.del_handler(pycozmo.event.EvtAudioCompleted, handler)


if __name__ == '__main__':
    main()
