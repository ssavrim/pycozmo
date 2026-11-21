Speech Dataset from App Captures
================================


Overview
--------

The Cozmo app synthesizes Cozmo's voice on the mobile device and streams it to the robot as
`OutputAudio` packets: 744 samples at 22,050 Hz per animation frame, 8-bit encoded, with
`OutputSilence` packets for silent frames. A capture of this traffic therefore holds the exact audio
sent to the speaker, without microphone or room noise.

`tools/pycozmo_tts_dataset.py` rebuilds that audio from one or more captures, cuts it into
utterances at pauses and writes a dataset in LJSpeech format, that TTS training recipes such as
[Piper](https://github.com/rhasspy/piper) accept:

```
cozmo_tts_dataset/
    wavs/cozmo_0001.wav     22,050 Hz, 16-bit, mono
    wavs/cozmo_0002.wav
    metadata.csv            cozmo_0001|Hello Anna.
    segments.csv            id|start_time|end_time|duration (capture times, for checking)
```

Cozmo's voice belongs to its rights holders. The dataset, and any voice model trained on it, are for
personal use with your own robot and should not be distributed.


Recording
---------

1. Set up a capture as described in [Capturing Cozmo Communication](capturing.md).
2. Write the sentences to record in a text file, one per line. Lines starting with `#` are ignored.
   Vary the content: names, questions, numbers, short and long sentences.
3. Start the capture, then make Cozmo say each sentence in order from the app (for example with
   "Cozmo Says" in Code Lab). Leave a clear pause, more than a second, between sentences, and avoid
   other sounds (animations, games) while recording.
4. Stop the capture. Several captures can be used, in the order they were recorded.

About 15 minutes of speech is enough to fine-tune an existing voice; one hour or more gives better
results. A few sentences are enough as the reference sample of zero-shot voice cloning models.


Building the Dataset
--------------------

List the utterances found, with the sentence each one would get:

```
$ pycozmo_tts_dataset.py -l -t sentences.txt session1.pcap session2.pcap
```

If the number of utterances does not match the number of sentences:

- sentences cut in two: increase `--max-gap` (longest pause inside an utterance, 0.5 s by default);
- short sounds counted as utterances: increase `--min-duration` (0.3 s by default) or
  `--threshold` (peak amplitude of a voiced frame, 512 by default);
- sentences merged: leave longer pauses when recording, or decrease `--max-gap`;
- otherwise, edit the sentence file to match what was recorded.

Then write the dataset:

```
$ pycozmo_tts_dataset.py -t sentences.txt -o cozmo_tts_dataset session1.pcap session2.pcap
```

Without `-t`, only `wavs/` and `segments.csv` are written, to transcribe later.

Listen to a few files and check `metadata.csv` before training: a single offset between sentences and
files spoils the whole dataset.


Notes
-----

- pcap and pcapng files are read, with Ethernet, Linux cooked (SLL, SLL2), raw IP and loopback link
  types. Captures of the Wi-Fi radio itself (802.11 headers) are not supported.
- Engine frames are resent until acknowledged; each audio packet is kept once, from its sequence
  number.
- Audio is decoded with `pycozmo.audio.cozmo_to_pcm()`, the inverse of the encoding used to play audio
  on the robot. The robot's 8-bit encoding limits the quality, which is also how Cozmo sounds.
