"""
Polylop: platform archetypes — declarative definitions plus an instance
registry.

CUSTOM (Polylop): no upstream counterpart in MiroFish-Offline. First step of
the archetype library (POL-ARCH01, PATCH-011).

Why
---
The simulation has known exactly two platforms so far, hard-wired as
``DefaultPlatformType.REDDIT`` / ``.TWITTER``, and the Polylop patch modules
told them apart by ``recsys_type``. That key stops working the moment two
archetypes share a base recsys (a business-network archetype runs on the same
personalized recsys as micro-broadcast). This module gives every ``Platform``
*instance* an archetype identity instead:

- ``ARCHETYPES``: declarative definitions. PATCH-011 ships exactly the two
  inherited ones — ``forum`` (today's Reddit setup) and ``micro_broadcast``
  (today's Twitter setup). New archetypes arrive only together with the code
  that consumes them; this project keeps finding config that nothing reads
  (influence_weight, posts_per_hour, the PlatformConfig weights), so no field
  is added here before something consumes it.
- a registry: ``Platform`` instance -> (archetype name, knobs). Instances are
  classified when they are constructed. Consumers (feed capacity, posting
  rate, future prompt/action layers) look their parameters up here instead of
  guessing from ``recsys_type``.
- ``on_platform(callback)``: consumers register a hook that runs once per new
  ``Platform`` instance with (platform, archetype_name, knobs). Hooks are
  replayed for instances that already exist, so registration order does not
  matter.

Behaviour is unchanged by design: this layer only carries parameters.
PATCH-011 must be a no-op for existing configs (regression-checked in
``backend/tests/test_archetype_layer.py``).
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("polylop.archetypes")

MARKER = "POLYLOP-ARCHETYPES"

# Archetype name -> definition. ``base_recsys`` classifies instances that are
# built through OASIS' DefaultPlatformType path; ``legacy_config_key`` names
# the simulation-config section whose entries become the instance knobs.
ARCHETYPES: Dict[str, Dict[str, Any]] = {
    "forum": {
        "base_recsys": "reddit",
        "legacy_config_key": "reddit_config",
    },
    "micro_broadcast": {
        "base_recsys": "twhin-bert",
        "legacy_config_key": "twitter_config",
    },
}

_state: Dict[str, Any] = {"applied": False, "config": {}}
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
    name = None
    for archetype_name, spec in ARCHETYPES.items():
        if spec["base_recsys"] == recsys:
            name = archetype_name
            break

    knobs: Dict[str, Any] = {}
    if name is not None:
        key = ARCHETYPES[name]["legacy_config_key"]
        knobs = dict((_state["config"] or {}).get(key) or {})
    else:
        logger.warning("%s unclassified platform (recsys=%s) - no knobs",
                       MARKER, recsys)

    entry = (platform, name, knobs)
    _instances.append(entry)
    _by_id[id(platform)] = entry
    print(f"{MARKER} platform recsys={recsys} -> archetype="
          f"{name or 'unclassified'}")
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
