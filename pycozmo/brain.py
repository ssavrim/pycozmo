"""

Brain class - high level behavior and emotion engine.

"""

import dataclasses
from threading import RLock, Thread
from queue import Queue, Empty
import time

from PIL import Image

from .logger import logger, logger_reaction, logger_behavior
from . import client
from . import event
from . import emotions
from . import needs
from . import behavior
from . import activity
from . import util
from . import robot


__all__ = [
    "Brain"
]


class Brain:
    """ Cozmo robot brain class. """

    def __init__(self, cli: client.Client, freeplay: bool = False):
        """
        ``freeplay`` lets the brain choose and run behaviors by itself, from the freeplay activities, when no reaction
        is running. It is off by default: the brain then only reacts.
        """
        super().__init__()

        self.cli = cli
        self.freeplay = freeplay
        self._lock = RLock()

        # TODO: Load configuration. See cozmo_resources/config/features.json

        util.check_assets()

        start_time = time.perf_counter()
        resource_dir = str(util.get_cozmo_asset_dir())
        self.activities = activity.load_activities(resource_dir)
        self.behaviors = behavior.load_behaviors(resource_dir, self.cli)
        self.emotion_types = emotions.load_emotion_types(resource_dir)
        self.emotion_events = emotions.load_emotion_events(resource_dir)
        self.mood = emotions.Mood(self.emotion_types, self.emotion_events)
        self.needs = needs.NeedsModel(needs.load_needs_config(resource_dir))
        self.context = activity.SelectionContext(
            needs=self.needs, mood=self.mood, runnable=self.is_behavior_runnable,
            running=self.is_behavior_running, running_reaction=lambda: self.running_reaction)
        self.reaction_manager = behavior.ReactionTriggerManager(
            behavior.load_reaction_triggers(resource_dir), self.context)
        self.cli.load_anims()
        logger.info("Loaded resources in {:.02f} s.".format(time.perf_counter() - start_time))

        self.cli.add_handler(event.EvtBehaviorDone, self.on_behavior_done)
        self.cli.add_handler(event.EvtCliffDetectedChange, self.on_cliff_detected)
        self.cli.add_handler(event.EvtRobotOrientationChange, self.on_robot_orientation_change)
        self.cli.add_handler(event.EvtRobotPickedUpChange, self.on_robot_picked_up_change)
        self.cli.add_handler(event.EvtRobotFallingChange, self.on_robot_falling_change)
        self.cli.add_handler(event.EvtRobotOnChargerChange, self.on_robot_on_charger_change)
        # TODO: ...

        # Reaction trigger queue
        self.reaction_queue: Queue[str] = Queue()

        self.stop_flag = False
        self.reaction_thread = Thread(daemon=True, name="ReactionThread", target=self.reaction_thread_run)
        self.heartbeat_thread = Thread(daemon=True, name="HeartbeatThread", target=self.heartbeat_thread_run)

        # Current activity
        self.activity = self.activities["Freeplay"]
        # Container of the freeplay activities, that chooses the behaviors.
        self.freeplay_activity = self.activities.get("StrictPriorityFreeplay")
        # Current behavior, the reaction trigger that runs it (if any) and the chooser that chose it (if any)
        self.behavior: behavior.Behavior | None = None
        self.running_reaction: str | None = None
        self.behavior_chooser: activity.BehaviorChooser | None = None

    def start(self) -> None:
        # Connect to robot
        self.reaction_thread.start()
        self.heartbeat_thread.start()

        # TODO: Enable stop on cliff.
        # TODO: Enable camera.
        # TODO: Drive off if on charger.

    def stop(self) -> None:
        # Disconnect from robot
        self.stop_flag = True
        if self.heartbeat_thread.is_alive():
            self.heartbeat_thread.join()
        if self.reaction_thread.is_alive():
            self.reaction_thread.join()

    def on_behavior_done(self, cli: client.Client) -> None:
        if self.behavior:
            logger_reaction.info("Done.")
            self.deactivate_behavior()

    def on_cliff_detected(self, cli: client.Client, state: bool) -> None:
        if state and not cli.robot_picked_up and cli.robot_moving:
            self.post_reaction("CliffDetected")

    def on_robot_orientation_change(self, cli: client.Client, orientation: robot.RobotOrientation) -> None:
        # FIXME: These are not working well at present.
        # if orientation == robot.RobotOrientation.ON_THREADS:
        #     self.post_reaction("ReturnedToTreads")
        # elif orientation == robot.RobotOrientation.ON_BACK:
        #     self.post_reaction("RobotOnBack")
        # elif orientation == robot.RobotOrientation.ON_FACE:
        #     self.post_reaction("RobotOnFace")
        # elif orientation == robot.RobotOrientation.ON_LEFT_SIDE or \
        #         orientation == robot.RobotOrientation.ON_RIGHT_SIDE:
        #     self.post_reaction("RobotOnSide")
        pass

    def on_robot_picked_up_change(self, cli: client.Client, state: bool) -> None:
        if state:
            self.post_reaction("RobotPickedUp")

    def on_robot_falling_change(self, cli: client.Client, state: bool) -> None:
        if state:
            self.post_reaction("RobotFalling")

    def on_robot_on_charger_change(self, cli: client.Client, state: bool) -> None:
        if state:
            self.post_reaction("PlacedOnCharger")

    def on_camera_image(self, cli: client.Client, new_im: Image.Image) -> None:
        """ Process images, coming from the robot camera. """
        # TODO: See cozmo_resources/config/engine/vision_config.json
        # TODO: motion detection
        # self.process_reaction_trigger("UnexpectedMovement")
        # TODO: face detection
        # self.process_reaction_trigger("FacePositionUpdate")?
        # TODO: pet detection
        # self.process_reaction_trigger("PetInitialDetection")
        # TODO: laser detection
        # TODO: cube marker detection
        # TODO: facial expression estimation
        # TODO: smile amount detection
        # TODO: blink amount detection
        # TODO: gaze detection?
        # TODO: image quality check
        pass

    def post_reaction(self, reaction_trigger: str) -> None:
        """ Post a reaction trigger to the reaction trigger queue. """
        logger_reaction.debug("Posting {}".format(reaction_trigger))
        self.reaction_queue.put(reaction_trigger)

    def reaction_thread_run(self) -> None:
        """ Reaction thread loop. Reaction trigger queue processing. """
        while not self.stop_flag:
            try:
                reaction_trigger = self.reaction_queue.get(timeout=0.05)
            except Empty:
                continue
            except Exception as e:
                logger.error("Failed to get from reaction trigger queue. {}".format(e))
                continue

            try:
                logger_reaction.info("Processing {}".format(reaction_trigger))
                self.process_emotion_event(reaction_trigger)
                self.process_reaction(reaction_trigger)
            except Exception as e:
                logger.error("Failed to dispatch reaction trigger '{}'. {}".format(reaction_trigger, e))
                continue

    def process_emotion_event(self, reaction_trigger: str) -> None:
        """ Process emotion events by applying affectors to emotion types. """
        if not self.mood.apply_event(reaction_trigger):
            logger_reaction.warning("Failed to find emotion event for {}.".format(reaction_trigger))

    def process_reaction(self, reaction_trigger: str) -> None:
        """ Tell the reaction trigger manager about a trigger and run the reaction that fires. """
        self.reaction_manager.notify(reaction_trigger)
        self.check_reactions()

    def check_reactions(self) -> str | None:
        """ Run the reaction that fires now (the last one, when several do). Returns its behavior identifier. """
        with self._lock:
            fired = self.reaction_manager.check(self.context)
            if not fired:
                return None
            # TODO: Handle should_resume_last.
            entry = fired[-1]
            logger_reaction.info("Reaction {} -> {}".format(entry.name, entry.behavior_id))
            self.activate_behavior(entry.behavior_id, reaction_trigger=entry.name)
            return entry.behavior_id

    def is_behavior_running(self, behavior_id: str) -> bool:
        return self.behavior is not None and self.behavior.get_id() == behavior_id

    def is_behavior_runnable(self, behavior_id: str) -> bool:
        """ Whether a behavior can run: it exists and, to drive off the charger, the robot is on it. """
        if behavior_id not in self.behaviors:
            return False
        if behavior_id == "DriveOffCharger":
            return bool(self.cli.robot_status & robot.RobotStatusFlag.IS_ON_CHARGER)
        return True

    def is_behavior_implemented(self, behavior_id: str) -> bool:
        """ Whether a behavior does something: the freeplay only chooses these, reactions run anyway. """
        candidate = self.behaviors.get(behavior_id)
        return candidate is not None and type(candidate) is not behavior.Behavior

    def choose_behavior(self) -> str | None:
        """ Choose and run the next behavior of the freeplay, when nothing runs. Returns its identifier. """
        with self._lock:
            if self.behavior is not None or self.freeplay_activity is None:
                return None
            context = dataclasses.replace(
                self.context, runnable=lambda b: self.is_behavior_implemented(b) and self.is_behavior_runnable(b))
            behavior_id = self.freeplay_activity.choose(context, self.activities)
            if behavior_id is None:
                return None
            sub_activity = self.activities.get(getattr(self.freeplay_activity, "current", None) or "")
            chooser = sub_activity.get_chooser() if sub_activity is not None else None
            logger_behavior.info("Freeplay chose {}".format(behavior_id))
            self.activate_behavior(behavior_id, chooser=chooser)
            return behavior_id

    def activate_behavior(
            self, behavior_id: str, reaction_trigger: str | None = None,
            chooser: activity.BehaviorChooser | None = None) -> None:
        with self._lock:
            new_behavior = self.behaviors.get(behavior_id)
            if new_behavior:
                self.deactivate_behavior()
                logger_behavior.info("Activating {}".format(behavior_id))
                self.behavior = new_behavior
                self.running_reaction = reaction_trigger
                self.behavior_chooser = chooser
                if chooser is not None:
                    chooser.started(behavior_id)
                self.cli.activate_behavior(self.behavior)
            else:
                logger_reaction.error("Failed to find behavior {}.".format(behavior_id))

    def deactivate_behavior(self) -> None:
        with self._lock:
            if self.behavior:
                behavior_id = self.behavior.get_id()
                logger_behavior.info("Deactivating {}".format(behavior_id))
                self.cli.deactivate_behavior(self.behavior)
                if self.behavior_chooser is not None:
                    self.behavior_chooser.stopped(behavior_id)
                self.behavior = None
                self.running_reaction = None
                self.behavior_chooser = None

    def heartbeat_thread_run(self) -> None:
        """ Heartbeat thread loop. """

        # Raise head.
        angle = (robot.MAX_HEAD_ANGLE.radians - robot.MIN_HEAD_ANGLE.radians) / 2.0
        self.cli.set_head_angle(angle)

        cnt = 1
        timer = util.FPSTimer(robot.FRAME_RATE)
        while not self.stop_flag:

            self.update_emotion_types()
            # TODO: Timers

            if cnt % robot.FRAME_RATE == 0:
                self.tick()

            if cnt % (30 * 60) == 0:
                self.post_reaction("Hiccup")

            cnt += 1
            timer.sleep()

    def tick(self) -> None:
        """ Once per second: update the needs, check the reactions that depend on the state, and choose a behavior. """
        try:
            self.needs.update()
            self.check_reactions()
            if self.freeplay:
                self.choose_behavior()
        except Exception as e:
            logger.error("Failed to tick the brain. {}".format(e))

    def update_emotion_types(self) -> None:
        """ Update emotion types from their decay functions. """
        for emotion_type in self.emotion_types.values():
            emotion_type.update()
