"""

Animation controller for audio, image, and animation playback.

"""

from typing import TYPE_CHECKING, Any
from collections.abc import Iterable
from threading import Thread, Lock
from collections import deque

from .logger import logger
from . import protocol_encoder
from . import util
from . import robot
from . import event
from . import procedural_face
from . import image_encoder

if TYPE_CHECKING:
    from .client import Client


class AnimationQueue:
    """ Synchronized animation queue class. """

    MAXLEN = 4500   # ~2.5 min of frames

    def __init__(self) -> None:
        self.lock = Lock()
        self.audio_queue: deque[protocol_encoder.OutputAudio | None] = deque(maxlen=self.MAXLEN)
        self.image_queue: deque[protocol_encoder.DisplayImage | None] = deque(maxlen=self.MAXLEN)
        self.pkt_queue: deque[Iterable[protocol_encoder.Packet] | None] = deque(maxlen=self.MAXLEN)

    def is_empty(self) -> bool:
        with self.lock:
            return not len(self.audio_queue) and \
                   not len(self.image_queue) and \
                   not len(self.pkt_queue)

    def put_audio(self, pkts: list[protocol_encoder.OutputAudio]) -> None:
        with self.lock:
            self.audio_queue.extend(pkts)

    def put_image(self, pkt: protocol_encoder.DisplayImage) -> None:
        with self.lock:
            self.image_queue.append(pkt)

    def put_anim_frame(
            self,
            audio_pkt: protocol_encoder.OutputAudio | None,
            image_pkt: protocol_encoder.DisplayImage | None,
            pkts: Iterable[protocol_encoder.Packet] | None) -> None:
        with self.lock:
            self.audio_queue.append(audio_pkt)
            self.image_queue.append(image_pkt)
            self.pkt_queue.append(pkts)

    def get(self) -> tuple[protocol_encoder.OutputAudio | None,
                           protocol_encoder.DisplayImage | None,
                           Iterable[protocol_encoder.Packet] | None]:
        with self.lock:
            # Audio
            try:
                audio_pkt = self.audio_queue.popleft()
            except IndexError:
                audio_pkt = None
            # Image
            try:
                image_pkt = self.image_queue.popleft()
            except IndexError:
                image_pkt = None
            # Action
            try:
                pkts = self.pkt_queue.popleft()
            except IndexError:
                pkts = None

        return audio_pkt, image_pkt, pkts

    def clear(self) -> None:
        with self.lock:
            self.audio_queue.clear()
            self.image_queue.clear()
            self.pkt_queue.clear()


class AnimationController:
    """ Animation controller class. """

    #: Frames kept queued on the robot ahead of playback. The robot buffers
    #: animation frames and plays them on its own 30 fps clock; streaming each
    #: frame just in time turned every Wi-Fi delay longer than one frame into an
    #: audible gap. The lead is measured with AnimationState.num_audio_frames_played.
    #: Its buffer holds about ten frames of sound: with a lead of 14, a robot discarded
    #: most of a spoken sentence.
    LEAD_FRAMES = 7
    #: Extra lead tolerated before slowing down, to absorb clock drift.
    LEAD_SLACK = 3

    def __init__(self, cli: "Client") -> None:
        self.cli = cli
        self.thread: Thread | None = None
        self.stop_flag = False
        self.queue = AnimationQueue()
        self.num_frames_played = -1
        self.num_audio_frames_played: int | None = None
        self.frames_sent = 0
        self._played_base: int | None = None
        self.playing_audio = False
        self.playing_animation = False
        #: True between the StartAnimation and the EndAnimation sent to the robot.
        self.streaming_animation = False
        #: Frames sent when EndAnimation went out; the animation is over when the robot has played them.
        self._end_frame: int | None = None
        #: The completion of the animation was already reported from the frame count.
        self._completed_by_count = False
        # Whether an image, displayed with display_image(), is shown. Procedural face is paused while it is.
        self.image_displayed = False
        self.last_image_pkt = protocol_encoder.DisplayImage(image=b"\x3f\x3f")
        self.face_generator = iter(procedural_face.ProceduralFaceGenerator())
        self.animations_enabled = False
        self.procedural_face_enabled = False

    def _clear_last_image_pkt(self) -> None:
        self.last_image_pkt = protocol_encoder.DisplayImage(image=b"\x3f\x3f")

    def start(self) -> None:
        self.frames_sent = 0
        self.num_audio_frames_played = None
        self._played_base = None
        self.thread = Thread(daemon=True, name=AnimationController.__name__, target=self._run)
        self.stop_flag = False
        self.thread.start()
        self.cli.add_handler(protocol_encoder.AnimationState, self._on_animation_state)
        self.cli.add_handler(protocol_encoder.Keyframe, self._on_keyframe)
        self.cli.add_handler(protocol_encoder.AnimationStarted, self._on_animation_started)
        self.cli.add_handler(protocol_encoder.AnimationEnded, self._on_animation_ended)
        self.cli.add_handler(event.EvtRobotAnimatingChange, self._on_animating_change)
        self.cli.add_handler(event.EvtRobotAnimBufferFullChange, self._on_anim_buffer_full_change)
        self.cli.add_handler(event.EvtRobotAnimatingIdleChange, self._on_amimating_idle_change)

    def stop(self) -> None:
        self.stop_flag = True
        if self.thread:
            self.thread.join()
            self.thread = None

    def _on_animation_state(self, cli: Any, pkt: protocol_encoder.AnimationState) -> None:
        if self._played_base is None:
            # Assume everything sent so far has been played; this underestimates
            # the lead by the frames in flight, which only adds margin.
            self._played_base = pkt.num_audio_frames_played - self.frames_sent
        self.num_audio_frames_played = pkt.num_audio_frames_played
        # The robot does not always send AnimationEnded (not when the animation has sound): the
        # animation is also over when it has played every frame sent up to EndAnimation.
        lead = self.lead()
        if self._end_frame is not None and lead is not None and lead <= self.frames_sent - self._end_frame:
            self._end_frame = None
            self._completed_by_count = True
            self._finish_animation()

    def _finish_animation(self):
        self.playing_animation = False
        self._clear_last_image_pkt()
        self.cli.conn.post_event(event.EvtAnimationCompleted, self.cli)

    def lead(self) -> int | None:
        """ Frames sent but not yet played by the robot, or None before the first report. """
        if self._played_base is None or self.num_audio_frames_played is None:
            return None
        return self.frames_sent - (self.num_audio_frames_played - self._played_base)

    def frame_sleeps(self) -> int:
        """ Timer periods to wait after a frame: 0 to fill the lead, 2 to drain an excess. """
        lead = self.lead()
        if lead is None:
            return 1
        # While an animation is streamed, frames are never sent in bursts: on a robot, an animation
        # with sound does not start when its frames arrive faster than 30 fps.
        if self.streaming_animation:
            return 1
        if lead < self.LEAD_FRAMES:
            return 0
        if lead > self.LEAD_FRAMES + self.LEAD_SLACK:
            return 2
        return 1

    def _on_keyframe(self, cli: Any, pkt: protocol_encoder.Keyframe) -> None:
        pass

    def _on_animation_started(self, cli: Any, pkt: protocol_encoder.AnimationStarted) -> None:
        self.playing_animation = True

    def _on_animation_ended(self, cli: Any, pkt: protocol_encoder.AnimationEnded) -> None:
        if self.streaming_animation:
            # The reply to the EndAnimation of cancel_anim() of a previous animation, which arrives
            # after the next animation has started: not the end of this one.
            return
        self._end_frame = None
        if self._completed_by_count:
            # Already reported from the frame count.
            self._completed_by_count = False
            return
        self._finish_animation()

    def _on_animating_change(self, cli: Any, state: bool) -> None:
        pass

    def _on_anim_buffer_full_change(self, cli: Any, state: bool) -> None:
        pass

    def _on_amimating_idle_change(self, cli: Any, state: bool) -> None:
        pass

    def _get_face_image(self) -> protocol_encoder.DisplayImage | None:
        im = next(self.face_generator)
        if not im:
            return None
        encoder = image_encoder.ImageEncoder(im)
        buf = bytes(encoder.encode())
        image_pkt = protocol_encoder.DisplayImage(image=buf)
        return image_pkt

    def _run(self) -> None:
        logger.debug("Animation controller started...")

        # Enable animation playback and AnimationState events. Requires Enable (0x25).
        pkt: protocol_encoder.Packet = protocol_encoder.EnableAnimationState()
        self.cli.conn.send(pkt)

        timer = util.FPSTimer(robot.FRAME_RATE)
        while not self.stop_flag:
            sent = self._run_frame()
            for _ in range(self.frame_sleeps() if sent else 1):
                timer.sleep()

        logger.debug("Animation controller stopped...")

    def _run_frame(self) -> bool:
        """ Send the audio, image, and animation packets for a single frame. Return False if none was sent. """
        audio_pkt, image_pkt, pkts = self.queue.get()

        if not self.animations_enabled:
            return False

        audio_out: protocol_encoder.Packet
        if audio_pkt:
            audio_out = audio_pkt
            if not self.playing_audio:
                self.playing_audio = True
        else:
            audio_out = protocol_encoder.OutputSilence()
            if self.playing_audio:
                self.playing_audio = False
                self.cli.conn.post_event(event.EvtAudioCompleted, self.cli)
        self.cli.conn.send(audio_out)

        if not image_pkt and self.procedural_face_enabled and not self.playing_animation \
                and not self.image_displayed:
            image_pkt = self._get_face_image()

        if image_pkt:
            self.cli.conn.send(image_pkt)
            self.last_image_pkt = image_pkt
        else:
            # If not refreshed, the robot stops displaying an image after 30 s.
            self.cli.conn.send(self.last_image_pkt)

        if pkts:
            for pkt in pkts:
                self.cli.conn.send(pkt)
                if isinstance(pkt, protocol_encoder.StartAnimation):
                    self.streaming_animation = True
                    self._completed_by_count = False
                elif isinstance(pkt, protocol_encoder.EndAnimation):
                    self.streaming_animation = False
                    self._end_frame = self.frames_sent + 1

        self.frames_sent += 1
        return True

    def play_audio(self, pkts: list[protocol_encoder.OutputAudio]) -> None:
        self.queue.put_audio(pkts)

    def display_image(self, pkt: protocol_encoder.DisplayImage) -> None:
        """ Display an image until clear_screen() is called or another image is displayed. """
        # Set before queueing, so that procedural face cannot replace the image in between.
        self.image_displayed = True
        self.queue.put_image(pkt)

    def clear_screen(self) -> None:
        """ Clear the screen and resume procedural face, if enabled. """
        self.queue.put_image(protocol_encoder.DisplayImage(image=b"\x3f\x3f"))
        self.image_displayed = False

    def play_anim_frame(
            self,
            audio_pkt: protocol_encoder.OutputAudio | None,
            image_pkt: protocol_encoder.DisplayImage | None,
            pkts: Iterable[protocol_encoder.Packet] | None) -> None:
        self.queue.put_anim_frame(audio_pkt, image_pkt, pkts)

    def cancel_anim(self) -> None:
        self.queue.clear()
        self.streaming_animation = False
        self._end_frame = None
        pkt = protocol_encoder.EndAnimation()
        self.cli.conn.send(pkt)

    def enable_animations(self, enabled: bool = True) -> None:
        self.animations_enabled = bool(enabled)

    def enable_procedural_face(self, enabled: bool = True) -> None:
        self.procedural_face_enabled = bool(enabled)
