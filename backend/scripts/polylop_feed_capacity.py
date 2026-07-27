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

Since PATCH-011 the per-platform lookup goes through the archetype registry
(``polylop_archetypes``) instead of branching on ``recsys_type`` — two
archetypes may share a base recsys, so the recsys is no longer a usable key.

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

_state: Dict[str, Any] = {"applied": False, "override": None, "changes": []}


def _valid_slots(value: Any, where: str) -> Optional[int]:
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        logger.warning("%s ignoring non-numeric feed_slots in %s: %r",
                       MARKER, where, value)
        return None
    if value < 1:
        logger.warning("%s ignoring feed_slots < 1 in %s: %d",
                       MARKER, where, value)
        return None
    return value


def _configured_anywhere(config: Dict[str, Any]) -> bool:
    import polylop_archetypes
    for spec in polylop_archetypes.ARCHETYPES.values():
        section = (config or {}).get(spec["legacy_config_key"]) or {}
        if _valid_slots(section.get("feed_slots"),
                        spec["legacy_config_key"]) is not None:
            return True
    # PATCH-012: generic platform entries carry their knobs inline
    for entry in (config or {}).get("platforms") or []:
        if _valid_slots((entry or {}).get("feed_slots"),
                        "platforms entry") is not None:
            return True
    return False


def _on_platform(platform, archetype: Optional[str],
                 knobs: Dict[str, Any]) -> None:
    wanted = _state["override"]
    if wanted is None:
        wanted = _valid_slots(knobs.get("feed_slots"),
                              archetype or "unclassified")
    if wanted is None:
        return
    previous = platform.refresh_rec_post_count
    if previous == wanted:
        return
    platform.refresh_rec_post_count = wanted
    label = archetype or str(platform.recsys_type)
    _state["changes"].append({"archetype": label,
                              "from": previous, "to": wanted})
    message = f"{MARKER} {label}: refresh_rec_post_count {previous} -> {wanted}"
    logger.info(message)
    print(message)


def apply_feed_capacity(config: Dict[str, Any]) -> bool:
    """Override refresh_rec_post_count per platform instance. Safe to call
    twice. Requires apply_archetypes() to have run (PATCH-011)."""
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

    if override is None and not _configured_anywhere(config):
        # nothing configured - leave OASIS exactly as it is
        return False

    _state["override"] = override

    import polylop_archetypes
    polylop_archetypes.on_platform(_on_platform)
    _state["applied"] = True

    print(f"{MARKER}-ACTIVE override={override} "
          "(fewer slots = more competition for reach)")
    return True


def capacity_stats() -> Dict[str, Any]:
    return {"applied": _state["applied"], "override": _state["override"],
            "changes": list(_state["changes"])}
