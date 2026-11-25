"""

Needs model: repair, energy and play levels with their decay, brackets and actions.

The model is driven by the needs configuration of the prepared resources (needs_config.json,
needs_decay_config.json and needs_action_config.json) and follows docs/specs/needs.md. It is pure: time is provided
by an injectable clock and randomness by an injectable random generator.

"""

import os
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .json_loader import load_json_file


__all__ = [
    "NeedId",
    "Bracket",
    "ThresholdRate",
    "ThresholdModifier",
    "ActionDelta",
    "NeedsConfig",
    "NeedsModel",

    "load_needs_config",
]


class NeedId(str, Enum):
    """ Need identifiers, as spelled in the configuration. """
    REPAIR = "Repair"
    ENERGY = "Energy"
    PLAY = "Play"


class Bracket(Enum):
    """ Level bracket of a need, from the fullest to the emptiest. """
    FULL = "Full"
    NORMAL = "Normal"
    WARNING = "Warning"
    CRITICAL = "Critical"


@dataclass(frozen=True)
class ThresholdRate:
    threshold: float
    decay_per_minute: float


@dataclass(frozen=True)
class ThresholdModifier:
    """ Multipliers applied to the decay of other needs while the level is at or above the threshold. """
    threshold: float
    multipliers: dict[NeedId, float]


@dataclass(frozen=True)
class ActionDelta:
    action_id: str
    deltas: dict[NeedId, float]
    ranges: dict[NeedId, float]
    cooldown_secs: float


@dataclass
class NeedsConfig:
    maximum: float
    minimum: float
    decay_period_seconds: float
    initial_levels: dict[NeedId, float]
    brackets: dict[NeedId, dict[Bracket, float]]
    connected_rates: dict[NeedId, list[ThresholdRate]]
    unconnected_rates: dict[NeedId, list[ThresholdRate]]
    connected_modifiers: dict[NeedId, list[ThresholdModifier]]
    fullness_cooldown_seconds: dict[NeedId, float]
    broken_part_thresholds: list[float]
    actions: dict[str, ActionDelta] = field(default_factory=dict)


def _rates(data: dict[str, Any], key: str) -> list[ThresholdRate]:
    rates = [ThresholdRate(float(e['Threshold']), float(e['DecayPerMinute'])) for e in data.get(key, [])]
    return sorted(rates, key=lambda r: r.threshold, reverse=True)


def _modifiers(data: dict[str, Any], key: str) -> list[ThresholdModifier]:
    modifiers = []
    for entry in data.get(key, []):
        multipliers = {NeedId(o['OtherNeedID']): float(o['Multiplier']) for o in entry['OtherNeedsAffected']}
        modifiers.append(ThresholdModifier(float(entry['Threshold']), multipliers))
    return sorted(modifiers, key=lambda m: m.threshold, reverse=True)


def load_needs_config(resource_dir: str) -> NeedsConfig:
    """ Load the needs configuration from prepared resources. """
    base = os.path.join(resource_dir, 'cozmo_resources', 'config', 'engine')
    main = load_json_file(os.path.join(base, 'needs_config.json'))
    decay = load_json_file(os.path.join(base, 'needs_decay_config.json'))
    action = load_json_file(os.path.join(base, 'needs_action_config.json'))

    initial = {}
    brackets = {}
    for need in NeedId:
        initial[need] = float(main['InitialNeedLevel' + need.value])
        brackets[need] = {b: float(main['BracketLevel' + need.value + b.value]) for b in Bracket}
    cooldowns = {n: float(main['FullnessDecayCooldown' + n.value]) for n in NeedId}
    part_thresholds = sorted(
        (float(v) for k, v in main.items() if k.startswith('BrokenPartThreshold')), reverse=True)

    rates = decay['DecayRates']
    modifiers = decay.get('DecayModifiers', {})
    connected_rates = {n: _rates(rates, 'ConnectedDecayRates' + n.value) for n in NeedId}
    unconnected_rates = {n: _rates(rates, 'UnconnectedDecayRates' + n.value) for n in NeedId}
    connected_modifiers = {n: _modifiers(modifiers, 'ConnectedDecayModifiers' + n.value) for n in NeedId}

    actions = {}
    for entry in action['actionDeltas']:
        deltas = {n: float(entry.get(n.value.lower() + 'Delta', 0.0)) for n in NeedId}
        ranges = {n: float(entry.get(n.value.lower() + 'Range', 0.0)) for n in NeedId}
        actions[entry['actionId']] = ActionDelta(
            entry['actionId'], deltas, ranges, float(entry.get('cooldownSecs', 0.0)))

    return NeedsConfig(
        maximum=float(main['MaximumNeedLevel']),
        minimum=float(main['MinimumNeedLevel']),
        decay_period_seconds=float(main['DecayPeriodSeconds']),
        initial_levels=initial,
        brackets=brackets,
        connected_rates=connected_rates,
        unconnected_rates=unconnected_rates,
        connected_modifiers=connected_modifiers,
        fullness_cooldown_seconds=cooldowns,
        broken_part_thresholds=part_thresholds,
        actions=actions,
    )


_EPSILON = 1e-5


class NeedsModel:
    """ Levels of the needs of the robot, decaying with time (docs/specs/needs.md). """

    def __init__(
            self, config: NeedsConfig,
            clock: Callable[[], float] = time.monotonic,
            rng: random.Random | None = None,
            connected: bool = True) -> None:
        self.config = config
        self.clock = clock
        self.rng = rng or random.Random()
        self.connected = connected
        now = clock()
        self.levels = dict(config.initial_levels)
        self.damaged_parts: list[bool] = [False] * len(config.broken_part_thresholds)
        self._next_decay = now + config.decay_period_seconds
        self._last_decay = {need: now for need in NeedId}
        self._cooldown_start: dict[NeedId, float] = {}
        self._cooldown_end: dict[NeedId, float] = {}

    # Levels and brackets

    def level(self, need: NeedId) -> float:
        self.update()
        return self.levels[need]

    def bracket(self, need: NeedId) -> Bracket:
        """ First bracket, from Full, whose level the need reaches; Critical when it reaches none. """
        level = self.level(need)
        thresholds = self.config.brackets[need]
        for bracket in (Bracket.FULL, Bracket.NORMAL, Bracket.WARNING):
            if level >= thresholds[bracket]:
                return bracket
        return Bracket.CRITICAL

    @property
    def num_damaged_parts(self) -> int:
        return sum(self.damaged_parts)

    # Decay

    def update(self) -> None:
        """ Apply the decay when the decay period has elapsed. """
        now = self.clock()
        if self.config.decay_period_seconds <= 0 or now < self._next_decay:
            return
        while self._next_decay <= now:
            self._next_decay += self.config.decay_period_seconds
        self._apply_decay(now)

    def _multipliers(self) -> dict[NeedId, float]:
        multipliers = {need: 1.0 for need in NeedId}
        if not self.connected:
            return multipliers
        for source in NeedId:
            for modifier in self.config.connected_modifiers[source]:
                if modifier.threshold <= self.levels[source]:
                    for target, factor in modifier.multipliers.items():
                        multipliers[target] *= factor
                    break
        return multipliers

    def _apply_decay(self, now: float) -> None:
        multipliers = self._multipliers()
        rates = self.config.connected_rates if self.connected else self.config.unconnected_rates
        for need in NeedId:
            end = self._cooldown_end.get(need)
            if end is not None:
                if now <= end:
                    continue    # the fullness cooldown pauses the decay
                self._last_decay[need] += end - self._cooldown_start[need]
                del self._cooldown_end[need]
                del self._cooldown_start[need]
            elapsed = now - self._last_decay[need]
            self._decay_need(need, elapsed / 60.0, rates[need], multipliers[need])
            self._last_decay[need] = now

    def _decay_need(self, need: NeedId, minutes: float, rates: list[ThresholdRate], multiplier: float) -> None:
        level = self.levels[need]
        index = 0
        while index < len(rates) and rates[index].threshold > level:
            index += 1
        if index >= len(rates):
            return      # below every threshold: no decay
        if minutes > 0:
            while True:
                rate = rates[index].decay_per_minute * multiplier
                if rate <= 0:
                    break
                floor = rates[index].threshold
                minutes_to_floor = (level - floor) / rate
                if minutes <= minutes_to_floor:
                    level -= minutes * rate
                    break
                minutes -= minutes_to_floor
                level = floor
                index += 1
                if minutes <= 0 or index >= len(rates):
                    break
        self.levels[need] = max(level, self.config.minimum)
        if need == NeedId.REPAIR:
            self._possibly_damage_parts()

    # Actions

    def apply_action(self, action_id: str, predicted: bool = False) -> bool:
        """ Apply the deltas of an action; return False, if the action is unknown. """
        action = self.config.actions.get(action_id)
        if action is None:
            return False
        self.update()
        if action_id in _REPAIR_PARTS and not predicted:
            part = _REPAIR_PARTS[action_id]
            if part < len(self.damaged_parts):
                self.damaged_parts[part] = False
        for need in NeedId:
            reached_full = self._apply_delta(need, action.deltas[need], action.ranges[need])
            if reached_full and not predicted:
                self._start_fullness_cooldown(need)
        if action_id in _REPAIR_PARTS and self.num_damaged_parts == 0:
            self.levels[NeedId.REPAIR] = self.config.maximum
        return True

    def _start_fullness_cooldown(self, need: NeedId) -> None:
        now = self.clock()
        self._cooldown_end[need] = now + self.config.fullness_cooldown_seconds[need]
        self._cooldown_start.setdefault(need, now)

    def _apply_delta(self, need: NeedId, delta: float, spread: float) -> bool:
        """ Apply one delta. Return True when a positive delta reaches the Full bracket. """
        config = self.config
        previous = self.levels[need]
        delta += self.rng.uniform(0.0, 2.0 * spread) - spread if spread else 0.0
        level = min(config.maximum, max(config.minimum, previous + delta))
        reached_full = False
        if delta > 0:
            full = config.brackets[need][Bracket.FULL]
            reached_full = level >= full
            if need == NeedId.ENERGY and reached_full and previous < full:
                level = config.maximum
        if need == NeedId.REPAIR:
            level = self._repair_band(level)
        self.levels[need] = level
        if delta < 0 and need == NeedId.REPAIR:
            self._possibly_damage_parts()
        return reached_full and delta > 0

    def _repair_band(self, level: float) -> float:
        """ Keep the repair level in the band of the number of damaged parts. """
        thresholds = self.config.broken_part_thresholds
        damaged = self.num_damaged_parts
        count = len(thresholds)
        if damaged > 0:
            upper = thresholds[min(damaged, count) - 1]
            if upper < level:
                level = upper - _EPSILON
        if damaged < count:
            lower = thresholds[damaged]
            if level < lower:
                level = lower + _EPSILON
        return level

    def _possibly_damage_parts(self) -> None:
        """ Damage random parts until their number matches the thresholds the repair level is under. """
        thresholds = self.config.broken_part_thresholds
        level = self.levels[NeedId.REPAIR]
        target = 0
        while target < len(thresholds) and thresholds[target] >= level:
            target += 1
        target = min(target, len(self.damaged_parts))
        missing = target - self.num_damaged_parts
        for _ in range(max(0, missing)):
            candidates = [i for i, damaged in enumerate(self.damaged_parts) if not damaged]
            self.damaged_parts[self.rng.choice(candidates)] = True


# Needs actions that repair a part, with the index of the part.
_REPAIR_PARTS = {"RepairHead": 0, "RepairLift": 1, "RepairTreads": 2}
