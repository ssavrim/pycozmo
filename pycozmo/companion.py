"""Decision policy only; callers provide fresh observations and enforce safety."""
from dataclasses import dataclass
from typing import Literal


__all__ = ["Action", "Observation", "CompanionBrain"]


Action = Literal["halt", "stop_search", "react", "greet", "search"]


@dataclass(frozen=True)
class Observation:
    """One current observation; ``now`` must use a monotonic clock in seconds."""

    now: float
    link_ok: bool
    camera_ok: bool
    unsafe: bool
    on_charger: bool
    low_battery: bool
    face: bool
    poked: bool
    cube_tapped: bool
    motion: bool
    searching: bool
    search_reason: str | None


class CompanionBrain:
    """Suggest reactions without issuing robot commands or managing sensors."""

    FACE_LOST_DELAY = 12.0
    GREETING_COOLDOWN = 45.0
    SEARCH_COOLDOWN = 20.0
    MAX_SEARCH_ATTEMPTS = 3

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.enabled = False
        self.state = 'off'
        self.reason = 'Companion mode disabled'
        self.started_at = 0.0
        self.last_face_at: float | None = None
        self.last_greeting_at: float | None = None
        self.last_search_at: float | None = None
        self.search_attempts = 0
        self.was_searching = False
        self.last_poke_reaction_at: float | None = None
        self.last_motion_reaction_at: float | None = None
        self.last_cube_reaction_at: float | None = None

    def start(self, now: float) -> None:
        """Start a fresh session using a monotonic timestamp."""
        self._reset()
        self.enabled = True
        self.started_at = now
        self.state, self.reason = 'observing', 'Observing surroundings'

    def stop(self, reason: str = 'Companion mode stopped') -> None:
        """Disable decisions and retain a human-readable stop reason."""
        self.enabled = False
        self.state, self.reason = 'off', reason

    def step(self, obs: Observation) -> Action | None:
        """Return the next suggested action, or None when no action is due."""
        if not self.enabled:
            return None
        if not obs.link_ok or not obs.camera_ok or obs.unsafe:
            self.stop('Safety: link, camera or sensor unavailable')
            return 'halt'
        if obs.low_battery:
            self.state, self.reason = 'resting', 'Low battery: resting; automatic docking is unavailable'
            return 'stop_search' if obs.searching else None
        if obs.poked and (self.last_poke_reaction_at is None or obs.now - self.last_poke_reaction_at >= 10):
            self.last_poke_reaction_at = obs.now
            self.state, self.reason = 'reacting', 'Touch detected'
            return 'react'
        if obs.cube_tapped and (self.last_cube_reaction_at is None or obs.now - self.last_cube_reaction_at >= 10):
            self.last_cube_reaction_at = obs.now
            self.state, self.reason = 'reacting', 'Cube tapped'
            return 'react'
        if obs.face:
            self.last_face_at = obs.now
            self.search_attempts = 0
            self.state, self.reason = 'interacting', 'Face detected'
            if self.last_greeting_at is None or obs.now - self.last_greeting_at >= self.GREETING_COOLDOWN:
                self.last_greeting_at = obs.now
                return 'greet'
            return None
        motion_ready = self.last_motion_reaction_at is None or obs.now - self.last_motion_reaction_at >= 30
        if obs.motion and not obs.searching and motion_ready:
            self.last_motion_reaction_at = obs.now
            self.state, self.reason = 'reacting', 'Visible movement'
            return 'react'
        if obs.searching:
            self.was_searching = True
            self.state, self.reason = 'searching', 'Searching for a person'
            return None
        if self.was_searching:
            self.was_searching = False
            self.last_search_at = obs.now
        if obs.on_charger:
            self.state, self.reason = 'observing', 'On charger: observing without movement'
            return None
        last_seen = self.last_face_at if self.last_face_at is not None else self.started_at
        delay_ok = obs.now - last_seen >= self.FACE_LOST_DELAY
        cooldown_ok = self.last_search_at is None or obs.now - self.last_search_at >= self.SEARCH_COOLDOWN
        if delay_ok and cooldown_ok and self.search_attempts < self.MAX_SEARCH_ATTEMPTS:
            self.search_attempts += 1
            self.last_search_at = obs.now
            self.state, self.reason = 'searching', 'Searching for a person'
            return 'search'
        self.state = 'observing'
        self.reason = ('Observing surroundings' if self.search_attempts < self.MAX_SEARCH_ATTEMPTS
                       else 'Paused after several searches')
        return None
