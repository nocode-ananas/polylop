"""
Polylop: put the configured posting rate to work.

CUSTOM (Polylop): no upstream counterpart in MiroFish-Offline.

Why
---
The config generator writes posts_per_hour and comments_per_hour per agent
(0.1-0.8 posts/h in real configs) — and nothing reads them, exactly like
influence_weight before Phase 2b. What actually steers the agents is OASIS'
environment prompt, and it is purely reactive:

    "...pick one you want to perform action that best reflects your current
     inclination based on your profile and posts content."

An agent is shown other people's posts and asked to react. So it reacts.
Measured over three real runs (30 rounds, 12 agents): 65-88 own actions, of
which essentially all were comments and likes, and five to six posts in total.
A campaign simulation with several waves needs contributions, not only
reactions.

What this does
--------------
Per round, each active agent is drawn against its own posts_per_hour (scaled
to the round length). Whoever is drawn gets one extra line appended to their
environment prompt for that round, telling them to contribute something of
their own instead of reacting. Soft steering, like the Phase 2a anchors — the
agent still writes from its persona, and still decides what to say.

Since PATCH-011 the drawn set is kept per platform, keyed by the platform's
channel. Before that, parallel runs shared one global set: both platform
loops overwrote each other's draw, and an agent could be nudged on Twitter
because its id had been drawn for Reddit (agent ids are the same personas on
every platform). Callers pass ``channel=env.channel``; calls without a
channel keep the old global behaviour.

Default is OFF. Enable per run:

    "reddit_config": {"posting_rate": true}     (or twitter_config)

or globally via POLYLOP_POSTING_RATE=on. This changes how a simulation
behaves, so it is opt-in, not a new default.
"""

import logging
import os
import random
from typing import Any, Dict, Iterable, Optional, Set

logger = logging.getLogger("polylop.posting_rate")

MARKER = "POLYLOP-POSTING-RATE"

NUDGE = ("\nYou have not contributed anything of your own for a while. "
         "Rather than only reacting to what you see, create a post that "
         "raises your own point, question or announcement on this topic, "
         "in your own voice.")

_GLOBAL_KEY = "global"

_state: Dict[str, Any] = {
    "enabled": False,
    "rates": {},        # agent_id -> posts per hour
    "posters": {},      # context key (id(channel) or "global") -> agent ids
    "rounds": 0,
    "nudges": 0,
}


def is_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    env = os.environ.get("POLYLOP_POSTING_RATE", "").strip().lower()
    if env in ("on", "1", "true", "yes"):
        return True
    if env in ("off", "0", "false", "no"):
        return False
    if not config:
        return False
    for key in ("reddit_config", "twitter_config"):
        section = config.get(key) or {}
        if section.get("posting_rate"):
            return True
    # PATCH-012: generic platform entries carry their knobs inline
    for entry in config.get("platforms") or []:
        if (entry or {}).get("posting_rate"):
            return True
    return False


def apply_posting_rate(config: Dict[str, Any]) -> bool:
    """Install the per-agent prompt nudge. Safe to call twice."""
    if not is_enabled(config):
        return False
    if _state["enabled"]:
        return True

    rates = {}
    for cfg in config.get("agent_configs", []) or []:
        try:
            agent_id = int(cfg.get("agent_id"))
        except (TypeError, ValueError):
            continue
        try:
            rates[agent_id] = max(0.0, float(cfg.get("posts_per_hour", 0.0)))
        except (TypeError, ValueError):
            rates[agent_id] = 0.0
    if not rates:
        logger.warning("%s no posts_per_hour in the config", MARKER)
        return False
    _state["rates"] = rates

    from oasis.social_agent.agent_environment import SocialEnvironment

    if not getattr(SocialEnvironment, "_polylop_posting_rate", False):
        original = SocialEnvironment.to_text_prompt

        async def to_text_prompt(self, *args, **kwargs):
            prompt = await original(self, *args, **kwargs)
            action = getattr(self, "action", None)
            agent_id = getattr(action, "agent_id", None)
            if agent_id is None:
                return prompt
            channel = getattr(action, "channel", None)
            posters = None
            if channel is not None:
                posters = _state["posters"].get(id(channel))
            if posters is None:
                posters = _state["posters"].get(_GLOBAL_KEY, set())
            if agent_id in posters:
                _state["nudges"] += 1
                return prompt + NUDGE
            return prompt

        SocialEnvironment.to_text_prompt = to_text_prompt
        SocialEnvironment._polylop_posting_rate = True

    _state["enabled"] = True
    span = (min(rates.values()), max(rates.values()))
    print(f"{MARKER}-ACTIVE agents={len(rates)} "
          f"posts_per_hour={span[0]:.2f}-{span[1]:.2f}")
    return True


def select_posters(active_agent_ids: Iterable[int],
                   minutes_per_round: int = 60,
                   channel: Any = None) -> Set[int]:
    """Draw who contributes in this round, from each agent's own rate.

    ``channel`` scopes the draw to one platform (pass ``env.channel``);
    without it the draw lands in the shared global scope, as before
    PATCH-011.
    """
    if not _state["enabled"]:
        return set()
    hours = max(minutes_per_round, 1) / 60.0
    chosen = set()
    for agent_id in active_agent_ids:
        rate = _state["rates"].get(agent_id, 0.0)
        if rate <= 0:
            continue
        if random.random() < min(1.0, rate * hours):
            chosen.add(agent_id)
    key = _GLOBAL_KEY if channel is None else id(channel)
    _state["posters"][key] = chosen
    _state["rounds"] += 1
    return chosen


def posting_stats() -> Dict[str, Any]:
    return {"enabled": _state["enabled"], "agents": len(_state["rates"]),
            "rounds": _state["rounds"], "nudges": _state["nudges"]}
