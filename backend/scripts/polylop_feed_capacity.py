"""
Polylop: make the feed capacity configurable.

CUSTOM (Polylop): no upstream counterpart in MiroFish-Offline.

Why
---
OASIS serves ``refresh_rec_post_count`` posts per refresh — 5 on Reddit, 2 on
Twitter — and those numbers are hard-coded in ``oasis/environment/env.py``.
Whenever a run produces fewer posts than the capacity, every agent sees
essentially everything, and no ranking or weighting can shift anything.

Measured on 2026-07-27 with identical real personas, 30 rounds, weights
1.0-2.3, only the capacity differing:

    capacity 5 (default) : rank correlation weight <-> reach  +0.60,
                           reach span across authors 75-86 (1.15x)
    capacity 2           : rank correlation                   +0.90,
                           reach span across authors 17-51 (3.0x)

So influence weighting (Phase 2b) does not fail in real runs — it simply has
nothing to decide when the feed shows everything anyway.

Default behaviour is unchanged: without configuration this module leaves the
OASIS values alone. Set the capacity per platform in the simulation config:

    "reddit_config":  {"feed_slots": 2}
    "twitter_config": {"feed_slots": 2}

or globally via POLYLOP_FEED_SLOTS. Off switch: POLYLOP_FEED_CAPACITY=off.
"""

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("polylop.feed_capacity")

MARKER = "POLYLOP-FEED-CAPACITY"

_state: Dict[str, Any] = {"applied": False, "reddit": None, "twitter": None,
                          "changes": []}


def _slots_from(config: Dict[str, Any], key: str) -> Optional[int]:
    section = config.get(key) or {}
    value = section.get("feed_slots")
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        logger.warning("%s ignoring non-numeric feed_slots in %s: %r",
                       MARKER, key, value)
        return None
    if value < 1:
        logger.warning("%s ignoring feed_slots < 1 in %s: %d",
                       MARKER, key, value)
        return None
    return value


def apply_feed_capacity(config: Dict[str, Any]) -> bool:
    """Override refresh_rec_post_count per platform. Safe to call twice."""
    if os.environ.get("POLYLOP_FEED_CAPACITY", "").strip().lower() in (
            "off", "0", "false", "no"):
        print(f"{MARKER} disabled via POLYLOP_FEED_CAPACITY")
        return False
    if _state["applied"]:
        return True

    env_slots = os.environ.get("POLYLOP_FEED_SLOTS")
    override = None
    if env_slots:
        try:
            override = max(1, int(env_slots))
        except ValueError:
            logger.warning("%s ignoring POLYLOP_FEED_SLOTS=%r",
                           MARKER, env_slots)

    reddit = override if override is not None else _slots_from(config, "reddit_config")
    twitter = override if override is not None else _slots_from(config, "twitter_config")

    if reddit is None and twitter is None:
        # nothing configured - leave OASIS exactly as it is
        return False

    _state["reddit"], _state["twitter"] = reddit, twitter

    import oasis.social_platform.platform as platform_mod
    from oasis.social_platform.typing import RecsysType

    if getattr(platform_mod.Platform, "_polylop_feed_capacity", False):
        _state["applied"] = True
        return True

    original_init = platform_mod.Platform.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        wanted = (reddit if self.recsys_type == RecsysType.REDDIT else twitter)
        if wanted is None:
            return
        previous = self.refresh_rec_post_count
        if previous == wanted:
            return
        self.refresh_rec_post_count = wanted
        _state["changes"].append({"platform": str(self.recsys_type),
                                  "from": previous, "to": wanted})
        message = (f"{MARKER} {self.recsys_type}: refresh_rec_post_count "
                   f"{previous} -> {wanted}")
        logger.info(message)
        print(message)

    platform_mod.Platform.__init__ = patched_init
    platform_mod.Platform._polylop_feed_capacity = True
    _state["applied"] = True

    print(f"{MARKER}-ACTIVE reddit={reddit} twitter={twitter} "
          "(fewer slots = more competition for reach)")
    return True


def capacity_stats() -> Dict[str, Any]:
    return {"applied": _state["applied"], "reddit": _state["reddit"],
            "twitter": _state["twitter"], "changes": list(_state["changes"])}
