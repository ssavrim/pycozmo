"""

Emotion representation and reading.

"""

import os
import time
from typing import Any, Callable, Self

from .logger import logger
from .json_loader import get_json_files, load_json_file


__all__ = [
    "EmotionType",
    "EmotionEvent",
    "Mood",

    "load_emotion_types",
    "load_emotion_events",
]


# A delta larger than this, in the direction of the value, restarts the decay.
STRENGTHEN_THRESHOLD = 0.05
# Thresholds of the simple mood.
SIMPLE_MOOD_HAPPY = 0.33
SIMPLE_MOOD_SAD_HAPPY = -0.33
SIMPLE_MOOD_SAD_CONFIDENT = -0.29
# Smallest decay graph value that is divided by.
DECAY_EPSILON = 1e-5


class Node:
    def __init__(self, x: float, y: float):
        self.x = float(x)
        self.y = float(y)


# DecayGraph and EmotionType are implemented in Rust (crates/cozmo-core/src/emotions.rs,
# crates/cozmo-py/src/emotions.rs): DecayGraph(nodes) and EmotionType(name, decay_graph, repetition_penalty,
# default_value=0.0, min_value=-1.0, max_value=1.0, clock=time.perf_counter), with the attributes and the set(),
# add() and update() methods of the Python classes.
from ._cozmo import DecayGraph, EmotionType  # noqa: E402


class EmotionEvent:
    """ EmotionEvent representation class. """

    __slots__ = [
        "name",
        "affectors",
        "repetition_penalty",
    ]

    def __init__(
            self, name: str, affectors: dict[str, float], repetition_penalty: DecayGraph | None = None) -> None:
        self.name = str(name)
        self.affectors = dict(affectors)
        self.repetition_penalty = repetition_penalty

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        affectors = {}
        for affector in data['emotionAffectors']:
            affectors[affector['emotionType']] = affector['value']
        penalty = None
        if 'repetitionPenalty' in data:
            penalty = DecayGraph([Node(x=n['x'], y=n['y']) for n in data['repetitionPenalty']['nodes']])
        return cls(name=data['name'], affectors=affectors, repetition_penalty=penalty)


class Mood:
    """ Set of emotions, changed by emotion events (docs/specs/mood.md). """

    def __init__(
            self,
            emotion_types: dict[str, EmotionType],
            emotion_events: dict[str, EmotionEvent],
            default_repetition_penalty: DecayGraph | None = None,
            clock: Callable[[], float] = time.perf_counter) -> None:
        self.emotion_types = emotion_types
        self.emotion_events = emotion_events
        if default_repetition_penalty is None:
            for emotion_type in emotion_types.values():
                default_repetition_penalty = emotion_type.repetition_penalty
                break
            else:
                default_repetition_penalty = DecayGraph([Node(0.0, 0.0), Node(30.0, 1.0)])
        self.default_repetition_penalty = default_repetition_penalty
        self.clock = clock
        self._last_event_time: dict[str, float] = {}

    def apply_event(self, name: str) -> bool:
        """
        Apply the affectors of an emotion event, scaled by its repetition penalty.

        The penalty is the value of the event's graph (or of the default one) at the time since the event last
        occurred; an event that did not occur before is not penalized. Return False, if the event is unknown.
        """
        event = self.emotion_events.get(name)
        if event is None:
            return False
        now = self.clock()
        last = self._last_event_time.get(name)
        self._last_event_time[name] = now
        graph = event.repetition_penalty or self.default_repetition_penalty
        elapsed = float("inf") if last is None else now - last
        penalty = float(graph.evaluate(elapsed))
        for emotion_name, value in event.affectors.items():
            emotion_type = self.emotion_types.get(emotion_name)
            if emotion_type is not None:
                emotion_type.add(penalty * value)
        return True

    def values(self) -> dict[str, float]:
        """ Current value of each emotion, after decay. """
        result = {}
        for name, emotion_type in self.emotion_types.items():
            emotion_type.update()
            result[name] = float(emotion_type.value)
        return result

    def simple_mood(self) -> str:
        """ "Happy", "Sad" or "Default", from the Happy and Confident emotions. """
        values = self.values()
        happy = values.get("Happy", 0.0)
        confident = values.get("Confident", 0.0)
        result = "Default"
        if happy > SIMPLE_MOOD_HAPPY:
            result = "Happy"
        if confident < SIMPLE_MOOD_SAD_CONFIDENT or happy < SIMPLE_MOOD_SAD_HAPPY:
            result = "Sad"
        return result


def load_emotion_types(
        resource_dir: str, clock: Callable[[], float] = time.perf_counter) -> dict[str, EmotionType]:

    start_time = time.perf_counter()

    json_data = load_json_file(
        os.path.join(resource_dir, 'cozmo_resources', 'config', 'engine', 'mood_config.json'))

    decay_graphs = {}
    for graph in json_data['decayGraphs']:
        nodes = [Node(x=n['x'], y=n['y']) for n in graph['nodes']]
        decay_graphs[graph['emotionType']] = DecayGraph(nodes)

    # Note: the repetition penalty might be linked not only to emotion events but also any activities or behaviors.
    default_rp = DecayGraph([Node(x=n['x'], y=n['y']) for n in json_data['defaultRepetitionPenalty']['nodes']])

    names = ["Happy", "Calm", "Brave", "Confident", "Charged", "Excited", "Social", "Winning", "WantToPlay"]
    emotion_types = {
        name: EmotionType(name, decay_graphs.get(name, decay_graphs['default']), default_rp, clock=clock)
        for name in names
    }

    logger.debug("Loaded emotion types in {:.02f} s.".format(time.perf_counter() - start_time))

    return emotion_types


def load_emotion_events(resource_dir: str) -> dict[str, EmotionEvent]:

    start_time = time.perf_counter()

    emotion_files = get_json_files(resource_dir,
                                   [os.path.join('cozmo_resources', 'config', 'engine', 'emotionevents/')])
    emotion_events = {}

    for ef in emotion_files:
        json_data = load_json_file(ef)
        if 'emotionEvents' not in json_data:
            emotion_events[json_data['name']] = EmotionEvent.from_json(json_data)
        else:
            for event in json_data['emotionEvents']:
                emotion_events[event['name']] = EmotionEvent.from_json(event)

    logger.debug("Loaded {} emotion events in {:.02f} s.".format(
        len(emotion_events), time.perf_counter() - start_time))

    return emotion_events
