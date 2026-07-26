"""
Polylop yield report — every run states what it actually produced.

CUSTOM (Polylop): no upstream counterpart in MiroFish-Offline.

Why this exists
---------------
Twice now a simulation reported "completed" while most of its agents were doing
nothing at all: the Mistral 3240 wave (PATCH-007) silently killed 91 % of the
requests, and it only became visible because somebody counted by hand
afterwards. A finished run and a productive run looked identical.

So at the end of every run this module writes a balance sheet next to the
database — agent steps, actions that had an effect, rejected LLM requests, and
the influence weighting the run used:

    POLYLOP-RUN-REPORT reddit  steps=180  actions=134 (74%)  llm_ok=180
                       llm_rejected=0  influence=on boost=1.0 weights=0.30-3.00

Anything suspicious is printed as an explicit WARN line, because a low yield
must not look like a normal ending (HANDBUCH L-043, "running is not
delivering"). The machine-readable version lands in
``polylop_run_report.json`` in the simulation directory.

The influence block doubles as the audit trail Phase 2b was missing: a report
now records which weighting produced these numbers.

Off switch: POLYLOP_RUN_REPORT=off
"""

import json
import logging
import os
import sqlite3
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger("polylop.run_report")

MARKER = "POLYLOP-RUN-REPORT"
MARKER_WARN = "POLYLOP-RUN-REPORT-WARN"

# Actions that are bookkeeping rather than an agent decision with an effect
_NON_ACTIONS = {"sign_up", "refresh"}

# Fewer than this many LLM-driven actions per agent step is reported as
# suspicious. One step can legitimately produce several actions (the agent runs
# a tool chain), so this is a rate, not a percentage.
LOW_YIELD_PER_STEP = 0.5

_requests = {
    "ok": 0,
    "rejected_3240": 0,      # Mistral: assistant message with neither content nor tool_calls
    "rejected_other_400": 0,
    "failed_other": 0,
    "first_errors": [],      # up to three short samples, for the report
}

_installed = False


def is_enabled() -> bool:
    return os.environ.get("POLYLOP_RUN_REPORT", "on").strip().lower() not in (
        "off", "0", "false", "no")


# --------------------------------------------------------------------------
# request counter
# --------------------------------------------------------------------------

def _classify(exc: Exception) -> str:
    text = str(exc)
    if "3240" in text or "must have either content or tool_calls" in text:
        return "rejected_3240"
    if "Error code: 400" in text or "invalid_request" in text:
        return "rejected_other_400"
    return "failed_other"


def _note_failure(exc: Exception) -> None:
    kind = _classify(exc)
    _requests[kind] += 1
    if len(_requests["first_errors"]) < 3:
        _requests["first_errors"].append({"kind": kind, "error": str(exc)[:200]})


def install_request_counter() -> bool:
    """Count every outgoing LLM request. Call once, before the run starts.

    Wraps the model backends at HTTP level, so the count is requests actually
    sent — not log lines, which is exactly the mistake that produced a wrong
    failure rate on 2026-07-25.
    """
    global _installed
    if _installed:
        return True
    if not is_enabled():
        print(f"{MARKER} disabled via POLYLOP_RUN_REPORT")
        return False

    targets = []
    try:
        from camel.models.openai_compatible_model import OpenAICompatibleModel
        targets.append(OpenAICompatibleModel)
    except Exception as exc:  # pragma: no cover - import shape guard
        logger.warning("%s no OpenAICompatibleModel: %s", MARKER_WARN, exc)
    try:
        from camel.models.openai_model import OpenAIModel
        targets.append(OpenAIModel)
    except Exception:
        pass

    if not targets:
        logger.warning("%s no model backend to count", MARKER_WARN)
        return False

    for cls in targets:
        if getattr(cls, "_polylop_request_counter", False):
            continue

        original_async = getattr(cls, "_arequest_chat_completion", None)
        original_sync = getattr(cls, "_request_chat_completion", None)

        if original_async is not None:
            async def counted_async(self, messages, tools=None, _orig=original_async):
                try:
                    result = await _orig(self, messages, tools)
                except Exception as exc:
                    _note_failure(exc)
                    raise
                _requests["ok"] += 1
                return result

            cls._arequest_chat_completion = counted_async

        if original_sync is not None:
            def counted_sync(self, messages, tools=None, _orig=original_sync):
                try:
                    result = _orig(self, messages, tools)
                except Exception as exc:
                    _note_failure(exc)
                    raise
                _requests["ok"] += 1
                return result

            cls._request_chat_completion = counted_sync

        cls._polylop_request_counter = True

    _installed = True
    print(f"{MARKER}-COUNTER-ACTIVE backends={len(targets)}")
    return True


def request_stats() -> Dict[str, Any]:
    return {k: (list(v) if isinstance(v, list) else v)
            for k, v in _requests.items()}


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def _read_db(db_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return None
    con = sqlite3.connect(db_path, timeout=10)
    try:
        actions = Counter(a for (a,) in con.execute("SELECT action FROM trace"))
        posts = con.execute("SELECT COUNT(*) FROM post").fetchone()[0]
        agents = con.execute("SELECT COUNT(*) FROM user").fetchone()[0]
    except sqlite3.Error as exc:
        logger.warning("%s could not read %s: %s", MARKER_WARN, db_path, exc)
        return None
    finally:
        con.close()

    steps = actions.get("refresh", 0)
    effective = sum(c for a, c in actions.items() if a not in _NON_ACTIONS)
    return {
        "agent_steps": steps,
        "actions_with_effect": effective,
        "action_breakdown": dict(actions),
        "posts": posts,
        "agents": agents,
    }


def _influence_block() -> Dict[str, Any]:
    try:
        import polylop_influence
    except Exception:
        return {"available": False}
    stats = polylop_influence.stats()
    weights = polylop_influence._state.get("weights") or {}
    block = {
        "available": True,
        "active": stats.get("applied", False),
        "boost": stats.get("boost"),
        "agents_weighted": stats.get("agents", 0),
        "patches": stats.get("patches", []),
        "weighted_feed_draws": stats.get("draws", {}).get("weighted", 0),
        "uniform_feed_draws": stats.get("draws", {}).get("uniform", 0),
    }
    if weights:
        block["weight_min"] = min(weights.values())
        block["weight_max"] = max(weights.values())
    return block


def _guard_block() -> Dict[str, Any]:
    try:
        import polylop_empty_reply_guard
        return {"available": True, **polylop_empty_reply_guard.guard_stats()}
    except Exception:
        return {"available": False}


def _llm_actions(data: Dict[str, Any], seed_posts: int) -> int:
    """Actions the agents decided themselves - seed posts are manual."""
    return max(0, data["actions_with_effect"] - seed_posts)


def _warnings_for(platforms: Dict[str, Dict[str, Any]],
                  requests: Dict[str, Any],
                  influence: Dict[str, Any],
                  seed_posts: int = 0) -> List[str]:
    warnings = []
    rejected = (requests["rejected_3240"] + requests["rejected_other_400"]
                + requests["failed_other"])
    total = rejected + requests["ok"]
    if rejected:
        share = rejected / total if total else 1.0
        warnings.append(
            f"{rejected} of {total} LLM requests failed ({share:.0%}) - "
            f"3240={requests['rejected_3240']} "
            f"other400={requests['rejected_other_400']} "
            f"other={requests['failed_other']}")

    for name, data in platforms.items():
        steps = data["agent_steps"]
        actions = _llm_actions(data, seed_posts)
        if steps == 0:
            warnings.append(f"{name}: no agent steps recorded at all")
            continue
        if actions / steps < LOW_YIELD_PER_STEP:
            warnings.append(
                f"{name}: only {actions} own actions across {steps} agent steps "
                f"({actions / steps:.2f} per step) - agents were largely idle "
                "or dying")

    if influence.get("available") and not influence.get("active"):
        warnings.append("influence weighting was NOT active in this run "
                        "(POLYLOP_INFLUENCE=off or no weights in the config)")
    return warnings


def write_run_report(simulation_dir: str,
                     platform: str = "reddit",
                     config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Write the yield balance for a finished run. Never raises."""
    if not is_enabled():
        return None
    try:
        db_names = {
            "reddit": ["reddit_simulation.db"],
            "twitter": ["twitter_simulation.db"],
            "parallel": ["twitter_simulation.db", "reddit_simulation.db"],
        }.get(platform, ["reddit_simulation.db", "twitter_simulation.db"])

        platforms = {}
        for db_name in db_names:
            data = _read_db(os.path.join(simulation_dir, db_name))
            if data:
                platforms[db_name.replace("_simulation.db", "")] = data

        seed_posts = 0
        if config:
            seed_posts = len(config.get("event_config", {})
                             .get("initial_posts", []) or [])

        influence = _influence_block()
        report = {
            "simulation_id": (config or {}).get("simulation_id"),
            "platform": platform,
            "model": os.environ.get("LLM_MODEL_NAME"),
            "seed_posts": seed_posts,
            "platforms": platforms,
            "llm_requests": request_stats(),
            "influence": influence,
            "empty_reply_guard": _guard_block(),
        }
        for data in platforms.values():
            data["llm_actions"] = _llm_actions(data, seed_posts)
        report["warnings"] = _warnings_for(platforms, _requests, influence,
                                           seed_posts)

        out_path = os.path.join(simulation_dir, "polylop_run_report.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)

        _print_report(report)
        return report
    except Exception as exc:  # a broken report must not fail a finished run
        logger.warning("%s could not write report: %s", MARKER_WARN, exc)
        print(f"{MARKER_WARN} report failed: {exc}")
        return None


def _print_report(report: Dict[str, Any]) -> None:
    req = report["llm_requests"]
    inf = report["influence"]
    guard = report["empty_reply_guard"]
    rejected = (req["rejected_3240"] + req["rejected_other_400"]
                + req["failed_other"])

    print("\n" + "=" * 60)
    print(f"{MARKER} — what this run actually produced")
    print("=" * 60)
    for name, data in report["platforms"].items():
        steps = data["agent_steps"]
        actions = data.get("llm_actions", data["actions_with_effect"])
        rate = f"{actions / steps:.2f}/step" if steps else "n/a"
        print(f"  {name:<8} agents={data['agents']:<4} steps={steps:<5} "
              f"own_actions={actions:<5} ({rate})  posts={data['posts']}")
        if data["action_breakdown"]:
            top = ", ".join(f"{a}={c}" for a, c in
                            sorted(data["action_breakdown"].items(),
                                   key=lambda kv: -kv[1])[:6])
            print(f"           {top}")
    print(f"  llm      ok={req['ok']} rejected={rejected} "
          f"(3240={req['rejected_3240']})")
    if inf.get("available"):
        state = "on" if inf.get("active") else "OFF"
        span = (f"{inf.get('weight_min'):.2f}-{inf.get('weight_max'):.2f}"
                if "weight_min" in inf else "n/a")
        print(f"  influence {state}  boost={inf.get('boost')}  "
              f"weights={span}  weighted_draws={inf.get('weighted_feed_draws')}")
    if guard.get("available"):
        print(f"  empty-reply guard dropped "
              f"{guard.get('dropped_on_record', 0)} on record, "
              f"{guard.get('dropped_on_send', 0)} on send")
    for warning in report["warnings"]:
        print(f"  {MARKER_WARN} {warning}")
    if not report["warnings"]:
        print("  no anomalies")
    print("=" * 60)
