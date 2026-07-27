"""
Polylop: platform archetypes — declarative definitions plus an instance
registry.

CUSTOM (Polylop): no upstream counterpart in MiroFish-Offline. First step of
the archetype library (POL-ARCH01, PATCH-011); platform construction for the
generic runner added in PATCH-012.

Why
---
The simulation has known exactly two platforms so far, hard-wired as
``DefaultPlatformType.REDDIT`` / ``.TWITTER``, and the Polylop patch modules
told them apart by ``recsys_type``. That key stops working the moment two
archetypes share a base recsys (a business-network archetype runs on the same
personalized recsys as micro-broadcast). This module gives every ``Platform``
*instance* an archetype identity instead:

- ``ARCHETYPES``: declarative definitions. The two inherited ones — ``forum``
  (today's Reddit setup) and ``micro_broadcast`` (today's Twitter setup) —
  mirror OASIS' DefaultPlatformType values exactly. New archetypes arrive
  only together with the code that consumes them; this project keeps finding
  config that nothing reads (influence_weight, posts_per_hour, the
  PlatformConfig weights), so no field is added here before something
  consumes it.
- a registry: ``Platform`` instance -> (archetype name, knobs). Instances are
  classified when they are constructed, or registered explicitly when built
  through ``build_platform``. Consumers (feed capacity, posting rate, future
  prompt/action layers) look their parameters up here instead of guessing
  from ``recsys_type``.
- ``on_platform(callback)``: consumers register a hook that runs once per new
  ``Platform`` instance with (platform, archetype_name, knobs). Hooks are
  replayed for instances that already exist, so registration order does not
  matter.
- ``resolve_platform_entries`` / ``build_platform`` (PATCH-012): the generic
  runner reads its platform list from ``config["platforms"]``; configs
  without that key resolve to exactly the inherited twitter+reddit pair.

Entry shape in ``config["platforms"]``:

    {"name": "reddit", "archetype": "forum"}

plus optional knobs consumed by the patch modules (``feed_slots``,
``posting_rate``), an optional ``llm`` ("common"/"boost", defaults per
archetype) and an optional ``profiles`` file name (defaults per archetype).
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("polylop.archetypes")

MARKER = "POLYLOP-ARCHETYPES"

# ``platform_params`` mirror oasis/environment/env.py (camel-oasis 0.2.5,
# DefaultPlatformType branch) exactly — pinned by a test against a
# DefaultPlatformType-built instance. ``actions`` are ActionType values,
# resolved lazily so importing this module never needs oasis.
ARCHETYPES: Dict[str, Dict[str, Any]] = {
    "forum": {
        "base_recsys": "reddit",
        "legacy_config_key": "reddit_config",
        "platform_params": {
            "recsys_type": "reddit",
            "allow_self_rating": True,
            "show_score": True,
            "max_rec_post_len": 100,
            "refresh_rec_post_count": 5,
        },
        "profile_format": "reddit_json",
        "default_profiles": "reddit_profiles.json",
        "default_llm": "boost",
        "actions": [
            "like_post", "dislike_post", "create_post", "create_comment",
            "like_comment", "dislike_comment", "search_posts", "search_user",
            "trend", "refresh", "do_nothing", "follow", "mute",
        ],
    },
    "micro_broadcast": {
        "base_recsys": "twhin-bert",
        "legacy_config_key": "twitter_config",
        "platform_params": {
            "recsys_type": "twhin-bert",
            "refresh_rec_post_count": 2,
            "max_rec_post_len": 2,
            "following_post_count": 3,
        },
        "profile_format": "twitter_csv",
        "default_profiles": "twitter_profiles.csv",
        "default_llm": "common",
        "actions": [
            "create_post", "like_post", "repost", "follow", "do_nothing",
            "quote_post",
        ],
    },
}

_state: Dict[str, Any] = {"applied": False, "config": {}, "pending": None}
# Strong refs are fine: a handful of Platform instances per process, and the
# process ends with the simulation. id() stays unique while the ref lives.
_instances: List[Tuple[Any, Optional[str], Dict[str, Any]]] = []
_by_id: Dict[int, Tuple[Any, Optional[str], Dict[str, Any]]] = {}
_callbacks: List[Callable[[Any, Optional[str], Dict[str, Any]], None]] = []


def _fire(callback, platform, name, knobs) -> None:
    # A consumer hook must never break a simulation.
    try:
        callback(platform, name, knobs)
    except Exception as exc:
        logger.error("%s consumer hook %r failed: %s", MARKER,
                     getattr(callback, "__module__", callback), exc)


def _register(platform) -> None:
    recsys = getattr(platform.recsys_type, "value", str(platform.recsys_type))

    pending = _state.get("pending")
    if pending is not None:
        name, knobs, label = pending
        origin = f"explicit{': ' + label if label else ''}"
    else:
        name = None
        for archetype_name, spec in ARCHETYPES.items():
            if spec["base_recsys"] == recsys:
                name = archetype_name
                break
        knobs = {}
        if name is not None:
            key = ARCHETYPES[name]["legacy_config_key"]
            knobs = dict((_state["config"] or {}).get(key) or {})
        else:
            logger.warning("%s unclassified platform (recsys=%s) - no knobs",
                           MARKER, recsys)
        origin = "classified"

    entry = (platform, name, knobs)
    _instances.append(entry)
    _by_id[id(platform)] = entry
    print(f"{MARKER} platform recsys={recsys} -> archetype="
          f"{name or 'unclassified'} ({origin})")
    for callback in _callbacks:
        _fire(callback, platform, name, knobs)


def apply_archetypes(config: Dict[str, Any]) -> bool:
    """Start classifying Platform instances. Idempotent, call before
    ``oasis.make``."""
    if _state["applied"]:
        return True
    _state["config"] = config or {}

    import oasis.social_platform.platform as platform_mod

    if not getattr(platform_mod.Platform, "_polylop_archetypes", False):
        original_init = platform_mod.Platform.__init__

        def patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            _register(self)

        platform_mod.Platform.__init__ = patched_init
        platform_mod.Platform._polylop_archetypes = True

    _state["applied"] = True
    print(f"{MARKER}-ACTIVE known={','.join(ARCHETYPES)}")
    return True


def on_platform(
        callback: Callable[[Any, Optional[str], Dict[str, Any]], None]) -> None:
    """Register a consumer hook; replayed for already-known instances."""
    _callbacks.append(callback)
    for platform, name, knobs in list(_instances):
        _fire(callback, platform, name, knobs)


def archetype_of(platform) -> Optional[str]:
    entry = _by_id.get(id(platform))
    return entry[1] if entry else None


def knobs_of(platform) -> Dict[str, Any]:
    entry = _by_id.get(id(platform))
    return dict(entry[2]) if entry else {}


def archetype_stats() -> Dict[str, Any]:
    return {
        "applied": _state["applied"],
        "instances": [
            {"archetype": name or "unclassified",
             "recsys": getattr(platform.recsys_type, "value",
                               str(platform.recsys_type))}
            for platform, name, _ in _instances
        ],
    }


# --------------------------------------------------------------------------
# PATCH-012: platform list resolution + explicit construction
# --------------------------------------------------------------------------

def resolve_platform_entries(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the validated platform entries for a run.

    ``config["platforms"]`` when present; otherwise exactly the inherited
    twitter+reddit pair (the pre-PATCH-012 behaviour of the parallel runner).
    Raises on unknown archetypes or duplicate names — a mistyped archetype
    must fail loudly, not silently simulate something else.
    """
    entries = (config or {}).get("platforms")
    if not entries:
        return [
            {"name": "twitter", "archetype": "micro_broadcast"},
            {"name": "reddit", "archetype": "forum"},
        ]
    validated = []
    seen = set()
    for entry in entries:
        entry = dict(entry or {})
        name = entry.get("name")
        archetype = entry.get("archetype")
        if not name or name in seen:
            raise ValueError(
                f"{MARKER} platforms entry needs a unique name: {entry!r}")
        if archetype not in ARCHETYPES:
            raise ValueError(
                f"{MARKER} unknown archetype {archetype!r} "
                f"(known: {', '.join(ARCHETYPES)})")
        seen.add(name)
        validated.append(entry)
    return validated


def entry_knobs(entry: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Effective knobs for one platform entry: the archetype's legacy config
    section as base (so old configs keep working), the entry itself on top."""
    spec = ARCHETYPES[entry["archetype"]]
    knobs = dict((config or {}).get(spec["legacy_config_key"]) or {})
    knobs.update(entry)
    return knobs


def build_platform(archetype_name: str, db_path: str,
                   knobs: Optional[Dict[str, Any]] = None,
                   label: Optional[str] = None):
    """Construct a Platform instance for an archetype and register it
    explicitly (wins over recsys classification)."""
    from oasis.social_platform.platform import Platform

    if not _state["applied"]:
        raise RuntimeError(f"{MARKER} build_platform needs apply_archetypes "
                           "first - the instance would go unregistered")
    spec = ARCHETYPES[archetype_name]
    _state["pending"] = (archetype_name, dict(knobs or {}), label)
    try:
        return Platform(db_path=db_path, **spec["platform_params"])
    finally:
        _state["pending"] = None


def archetype_actions(archetype_name: str):
    """The archetype's available actions as ActionType values (lazy oasis
    import)."""
    from oasis.social_platform.typing import ActionType
    return [ActionType(a) for a in ARCHETYPES[archetype_name]["actions"]]
