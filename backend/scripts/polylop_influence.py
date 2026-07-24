"""
Polylop Phase 2b — influence_weight as a numeric behaviour weight in OASIS.

CUSTOM (Polylop): this module has no upstream counterpart in MiroFish-Offline.

Background
----------
`simulation_config.json` already carries a per-agent ``influence_weight``
(0.8 - 3.0, produced by the LLM config generator), but OASIS never reads it:
the value only decided which agent posts the seed content. Everything after
that treated every persona as equally loud.

This module monkey-patches the OASIS package *in the simulation subprocess*
(no OASIS fork, see HANDOVER-2026-06-05) so that ``influence_weight`` becomes a
numeric factor in the two places that actually decide how far a post travels:

  A) Candidate ranking - ``recsys.calculate_hot_score`` becomes author-aware
     and ``rec_sys_reddit`` passes the post author in. An influential author
     gets an additive bonus of ``boost * log10(weight)`` on the Reddit hot
     score, i.e. it competes with the recency term in the same unit
     (1.0 hot-score point == 12.5 sandbox hours == one order of magnitude of
     net votes). Only effective once a run has more posts than
     ``max_rec_post_len`` (100 on Reddit) - measured runs so far had 7-33, so
     this path alone would have been a no-op.

  B) Feed selection - ``Platform.refresh`` draws ``refresh_rec_post_count``
     posts out of the agent's rec set with ``random.sample`` (uniform). We
     replace that draw with a weighted draw without replacement, weight =
     ``influence_weight`` of the post author. This is the path that is active
     in every run, regardless of post volume.

  C) Twitter ranking - ``rec_sys_personalized_twh`` multiplies a cosine
     similarity matrix by a per-post recency score (``date_score``) and then
     takes the top ``max_rec_post_len`` (2 on Twitter). We pre-fill that
     module-global score list with the influence factor folded in, so the
     original function ranks influence-weighted without being rewritten.

Everything is opt-out via env vars:
    POLYLOP_INFLUENCE=off        disable all patches (control runs)
    POLYLOP_INFLUENCE_BOOST=1.0  hot-score bonus scale (A only)

The module never fails a simulation: if OASIS internals do not look the way
this code expects, it logs a loud warning and leaves that patch un-applied.
Silent degradation is deliberately avoided (HANDBUCH L-043).
"""

import logging
import math
import os
import random as _random
from typing import Any, Dict, List, Optional

logger = logging.getLogger("polylop.influence")

# Marker strings - grep for these to prove the patch is live (HANDBUCH L-001)
MARKER_ACTIVE = "POLYLOP-INFLUENCE-ACTIVE"
MARKER_SKIP = "POLYLOP-INFLUENCE-SKIPPED"
MARKER_FAIL = "POLYLOP-INFLUENCE-FAILED"
MARKER_DRAW = "POLYLOP-INFLUENCE-DRAW"

DEFAULT_WEIGHT = 1.0

_state: Dict[str, Any] = {
    "applied": False,
    "weights": {},          # agent_id (== user_id in OASIS) -> float
    "boost": 1.0,
    "patches": [],          # names of patches actually applied
    "current_platform": None,  # set by the refresh wrapper, read by the shim
    "draw_stats": {"weighted": 0, "uniform": 0},
}


# --------------------------------------------------------------------------
# public helpers
# --------------------------------------------------------------------------

def load_weights(config: Dict[str, Any]) -> Dict[int, float]:
    """Extract {agent_id: influence_weight} from a simulation config dict."""
    weights = {}
    for cfg in config.get("agent_configs", []) or []:
        try:
            agent_id = int(cfg.get("agent_id"))
        except (TypeError, ValueError):
            continue
        try:
            weight = float(cfg.get("influence_weight", DEFAULT_WEIGHT))
        except (TypeError, ValueError):
            weight = DEFAULT_WEIGHT
        # A non-positive weight would break the weighted draw and the log10.
        if weight <= 0:
            weight = 1e-3
        weights[agent_id] = weight
    return weights


def weight_of(agent_id: Optional[int]) -> float:
    """influence_weight of an agent/user id, 1.0 when unknown."""
    if agent_id is None:
        return DEFAULT_WEIGHT
    try:
        return _state["weights"].get(int(agent_id), DEFAULT_WEIGHT)
    except (TypeError, ValueError):
        return DEFAULT_WEIGHT


def is_enabled() -> bool:
    return os.environ.get("POLYLOP_INFLUENCE", "on").strip().lower() not in (
        "off", "0", "false", "no")


def stats() -> Dict[str, Any]:
    """Runtime counters, for tests and post-run reporting."""
    return {
        "applied": _state["applied"],
        "patches": list(_state["patches"]),
        "agents": len(_state["weights"]),
        "boost": _state["boost"],
        "draws": dict(_state["draw_stats"]),
    }


# --------------------------------------------------------------------------
# A) author-aware hot score  (+ rec_sys_reddit passing the author in)
# --------------------------------------------------------------------------

def _build_hot_score(orig_hot_score):
    def polylop_calculate_hot_score(num_likes, num_dislikes, created_at,
                                    author_id=None):
        """Reddit hot score plus an additive influence bonus.

        The bonus is ``boost * log10(influence_weight)``, so it lives in the
        same unit as the original ``order`` term (one order of magnitude of
        net votes) instead of scaling the huge epoch term.
        """
        base = orig_hot_score(num_likes, num_dislikes, created_at)
        if author_id is None:
            return base
        weight = weight_of(author_id)
        if weight == DEFAULT_WEIGHT:
            return base
        bonus = _state["boost"] * math.log10(max(weight, 1e-6))
        return round(base + bonus, 7)

    return polylop_calculate_hot_score


def _build_rec_sys_reddit(hot_score_fn):
    import heapq
    from datetime import datetime

    def polylop_rec_sys_reddit(post_table: List[Dict[str, Any]],
                               rec_matrix: List[List],
                               max_rec_post_len: int) -> List[List]:
        """Same as oasis.rec_sys_reddit, but the hot score knows the author."""
        post_ids = [post['post_id'] for post in post_table]

        if len(post_ids) <= max_rec_post_len:
            return [post_ids] * len(rec_matrix)

        all_hot_score = []
        for post in post_table:
            try:
                created_at_dt = datetime.strptime(post['created_at'],
                                                  "%Y-%m-%d %H:%M:%S.%f")
            except Exception:
                created_at_dt = datetime.strptime(post['created_at'],
                                                  "%Y-%m-%d %H:%M:%S")
            hot_score = hot_score_fn(post['num_likes'],
                                     post['num_dislikes'],
                                     created_at_dt,
                                     post.get('user_id'))
            all_hot_score.append((hot_score, post['post_id']))

        top_posts = heapq.nlargest(max_rec_post_len,
                                   all_hot_score,
                                   key=lambda x: x[0])
        top_post_ids = [post_id for _, post_id in top_posts]
        return [top_post_ids] * len(rec_matrix)

    return polylop_rec_sys_reddit


# --------------------------------------------------------------------------
# B) weighted feed draw inside Platform.refresh
# --------------------------------------------------------------------------

def weighted_sample(population: List[int], k: int,
                    weights: Dict[int, float]) -> List[int]:
    """Draw k distinct items, probability proportional to weight.

    Plain successive weighted draw without replacement; population sizes here
    are tens to low hundreds, so an O(n*k) implementation is fine.
    """
    if k >= len(population):
        return list(population)
    remaining = list(population)
    remaining_weights = [max(weights.get(item, DEFAULT_WEIGHT), 1e-9)
                         for item in remaining]
    picked = []
    for _ in range(k):
        total = sum(remaining_weights)
        threshold = _random.random() * total
        cumulative = 0.0
        index = len(remaining) - 1
        for i, w in enumerate(remaining_weights):
            cumulative += w
            if cumulative >= threshold:
                index = i
                break
        picked.append(remaining.pop(index))
        remaining_weights.pop(index)
    return picked


class _RandomShim:
    """Stands in for the ``random`` module inside oasis.social_platform.platform.

    Only ``sample`` is intercepted, and only when every element of the
    population is a post id we can resolve to an author. Everything else is
    delegated to the real module untouched.
    """

    def __init__(self, real_random):
        self._real = real_random

    def __getattr__(self, item):
        return getattr(self._real, item)

    def sample(self, population, k, *args, **kwargs):
        platform = _state.get("current_platform")
        if platform is None or args or kwargs:
            _state["draw_stats"]["uniform"] += 1
            return self._real.sample(population, k, *args, **kwargs)
        try:
            authors = _post_authors(platform, list(population))
        except Exception as exc:  # never break a simulation over this
            logger.warning("%s weighted draw unavailable: %s",
                           MARKER_FAIL, exc)
            _state["draw_stats"]["uniform"] += 1
            return self._real.sample(population, k, *args, **kwargs)

        if len(authors) != len(population):
            # unknown population -> not our business
            _state["draw_stats"]["uniform"] += 1
            return self._real.sample(population, k, *args, **kwargs)

        post_weights = {post_id: weight_of(author)
                        for post_id, author in authors.items()}
        _state["draw_stats"]["weighted"] += 1
        return weighted_sample(list(population), k, post_weights)


def _post_authors(platform, post_ids: List[int]) -> Dict[int, int]:
    """post_id -> author user_id, straight from the platform's own sqlite db."""
    if not post_ids:
        return {}
    cursor = platform.db.cursor()
    try:
        placeholders = ", ".join("?" for _ in post_ids)
        cursor.execute(
            f"SELECT post_id, user_id FROM post WHERE post_id IN ({placeholders})",
            post_ids)
        return {row[0]: row[1] for row in cursor.fetchall()}
    finally:
        cursor.close()


def _build_refresh(orig_refresh):
    async def polylop_refresh(self, agent_id: int):
        # Platform.refresh has no await between here and its random.sample
        # call, so a module-level "current platform" is safe under asyncio.
        _state["current_platform"] = self
        try:
            return await orig_refresh(self, agent_id)
        finally:
            _state["current_platform"] = None

    return polylop_refresh


# --------------------------------------------------------------------------
# C) influence-weighted recency scores for the Twitter (twhin) recsys
# --------------------------------------------------------------------------

def _build_twh(orig_twh, recsys_mod):
    import numpy as np

    def polylop_rec_sys_personalized_twh(user_table, post_table,
                                         latest_post_count, trace_table,
                                         rec_matrix, max_rec_post_len,
                                         current_time, *args, **kwargs):
        """Pre-fill the module globals that the original function would fill,
        folding influence_weight into the per-post score.

        Mirrors oasis/social_platform/recsys.py (camel-oasis 0.2.5), lines
        440-472. Once t_items is complete the original skips its own update
        loop, so the weighted date_score survives into the ranking step.
        """
        try:
            if (not recsys_mod.u_items) or len(recsys_mod.u_items) != len(user_table):
                recsys_mod.u_items = {u['user_id']: u["num_followers"]
                                      for u in user_table}
            if (not recsys_mod.user_previous_post_all
                    or len(recsys_mod.user_previous_post_all) != len(user_table)):
                recsys_mod.user_previous_post_all = {
                    i: [] for i in range(len(user_table))}
                recsys_mod.user_previous_post = {
                    i: "" for i in range(len(user_table))}
            if (not recsys_mod.user_profiles
                    or len(recsys_mod.user_profiles) != len(user_table)):
                for user in user_table:
                    bio = user['bio']
                    recsys_mod.user_profiles.append(
                        'This user does not have profile' if bio is None else bio)

            if len(recsys_mod.t_items) < len(post_table):
                for post in post_table[-latest_post_count:]:
                    recsys_mod.t_items[post['post_id']] = post['content']
                    recsys_mod.user_previous_post_all[post['user_id']].append(
                        post['content'])
                    recsys_mod.user_previous_post[post['user_id']] = post['content']
                    base = np.log(
                        (271.8 - (current_time - int(post['created_at']))) / 100)
                    weight = weight_of(post['user_id'])
                    # keep the ordering monotone even where the recency term
                    # has gone negative (old posts, >171 sandbox steps)
                    scaled = base * weight if base > 0 else base / weight
                    recsys_mod.date_score.append(scaled)
        except Exception as exc:
            logger.warning("%s twhin pre-fill failed (%s) - falling back to "
                           "unweighted OASIS scoring", MARKER_FAIL, exc)

        return orig_twh(user_table, post_table, latest_post_count, trace_table,
                        rec_matrix, max_rec_post_len, current_time,
                        *args, **kwargs)

    return polylop_rec_sys_personalized_twh


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def apply_influence_patches(config: Dict[str, Any]) -> bool:
    """Install the Polylop influence patches into the imported OASIS package.

    Call once per simulation process, after the config is loaded and before
    ``oasis.make``. Idempotent. Returns True when patches are live.
    """
    if _state["applied"]:
        return True

    if not is_enabled():
        logger.warning("%s POLYLOP_INFLUENCE=off - running with stock OASIS "
                       "weighting", MARKER_SKIP)
        print(f"{MARKER_SKIP} (POLYLOP_INFLUENCE=off)")
        return False

    weights = load_weights(config)
    if not weights:
        logger.warning("%s no agent_configs/influence_weight in config",
                       MARKER_SKIP)
        print(f"{MARKER_SKIP} (no influence_weight in config)")
        return False

    try:
        _state["boost"] = float(os.environ.get("POLYLOP_INFLUENCE_BOOST", "1.0"))
    except ValueError:
        _state["boost"] = 1.0

    _state["weights"] = weights

    import oasis.social_platform.platform as platform_mod
    import oasis.social_platform.recsys as recsys_mod

    applied = []

    # A) author-aware hot score + reddit candidate ranking
    try:
        hot_score_fn = _build_hot_score(recsys_mod.calculate_hot_score)
        recsys_mod.calculate_hot_score = hot_score_fn
        rec_reddit = _build_rec_sys_reddit(hot_score_fn)
        recsys_mod.rec_sys_reddit = rec_reddit
        platform_mod.rec_sys_reddit = rec_reddit  # imported by name at import time
        applied.append("hot_score+rec_sys_reddit")
    except Exception as exc:
        logger.error("%s hot score patch: %s", MARKER_FAIL, exc)

    # B) weighted feed draw
    try:
        platform_mod.random = _RandomShim(platform_mod.random)
        platform_mod.Platform.refresh = _build_refresh(
            platform_mod.Platform.refresh)
        applied.append("refresh_weighted_draw")
    except Exception as exc:
        logger.error("%s refresh patch: %s", MARKER_FAIL, exc)

    # C) influence-weighted twitter ranking
    try:
        twh = _build_twh(recsys_mod.rec_sys_personalized_twh, recsys_mod)
        recsys_mod.rec_sys_personalized_twh = twh
        platform_mod.rec_sys_personalized_twh = twh
        applied.append("twhin_weighted_scores")
    except Exception as exc:
        logger.error("%s twhin patch: %s", MARKER_FAIL, exc)

    _state["patches"] = applied
    _state["applied"] = bool(applied)

    spread = (min(weights.values()), max(weights.values()))
    message = (f"{MARKER_ACTIVE} agents={len(weights)} "
               f"weight_range={spread[0]:.2f}-{spread[1]:.2f} "
               f"boost={_state['boost']} patches={','.join(applied)}")
    logger.info(message)
    print(message)
    return _state["applied"]
