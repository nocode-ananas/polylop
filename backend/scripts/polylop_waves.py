"""
Polylop: campaign waves — timed message injection (PATCH-015, POL-SEQ01).

CUSTOM (Polylop): no upstream counterpart in MiroFish-Offline.

Why
---
A campaign is a sequence of planned sendouts over time and channels, but the
runner could only inject content at round 0 (initial_posts). This module adds
a "waves" config key: each wave is a **point sendout** — content + sandbox
hour + platform + sender. Repetition in module A is CONTENT repetition (the
same or a varied message as another wave); duration/pressure mechanics
("the longer the wave runs, the more contacts") were deliberately rejected
for module A — they are quantity/dose claims and belong to module C (see the
warning box in POL-SEQ01 §3: in the simulation "more insertions -> more
contacts" would be true by construction).

Waves address SIMULATION ROUNDS via sandbox hours: at_hour h fires in the
round that covers runner minute h*60 (round_num * minutes_per_round). Note
that the DB's created_at timestamps run on OASIS' sandbox clock (real time
x60) and do NOT match runner hours — wave evidence therefore lives in the
manifest (wave -> post_ids + injection round) and in row order, not in DB
timestamps.

Config:

    "waves": [
      {"at_hour": 0,  "platform": "biznet", "poster_agent_id": 9,
       "content": "…", "wave": "W1"},
      {"at_hour": 48, "platform": "letter", "poster_agent_id": 9,
       "content": "…", "wave": "W2"}
    ]

Runs without a "waves" key are untouched. Unknown platform names or broken
entries fail loudly at startup — a mistyped wave must not silently simulate
a different campaign.
"""

import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger("polylop.waves")

MARKER = "POLYLOP-WAVES"

_injections: List[Dict[str, Any]] = []  # manifest entries, collected per run


def parse_waves(config: Optional[Dict[str, Any]],
                platform_names: List[str]) -> List[Dict[str, Any]]:
    """Validate config["waves"]; [] when absent. Raises ValueError on junk."""
    raw = (config or {}).get("waves")
    if not raw:
        return []
    known = set(platform_names)
    waves = []
    for i, entry in enumerate(raw):
        entry = dict(entry or {})
        name = entry.get("wave") or f"W{i + 1}"
        platform = entry.get("platform")
        content = entry.get("content")
        try:
            at_hour = float(entry.get("at_hour"))
        except (TypeError, ValueError):
            raise ValueError(f"{MARKER} wave {name!r}: at_hour missing or "
                             f"not a number: {entry.get('at_hour')!r}")
        try:
            poster = int(entry.get("poster_agent_id"))
        except (TypeError, ValueError):
            raise ValueError(f"{MARKER} wave {name!r}: poster_agent_id "
                             "missing or not an integer")
        if at_hour < 0:
            raise ValueError(f"{MARKER} wave {name!r}: at_hour < 0")
        if platform not in known:
            raise ValueError(f"{MARKER} wave {name!r}: unknown platform "
                             f"{platform!r} (this run: "
                             f"{', '.join(sorted(known))})")
        if not content or not str(content).strip():
            raise ValueError(f"{MARKER} wave {name!r}: empty content")
        waves.append({"wave": name, "platform": platform, "at_hour": at_hour,
                      "poster_agent_id": poster, "content": str(content)})
    return waves


def check_horizon(waves: List[Dict[str, Any]], total_hours: float) -> None:
    """Warn loudly about waves that can never fire."""
    for wave in waves:
        if wave["at_hour"] >= total_hours:
            message = (f"{MARKER}-WARN wave {wave['wave']} "
                       f"at_hour={wave['at_hour']} is beyond the simulation "
                       f"horizon ({total_hours}h) and will never fire")
            logger.warning(message)
            print(message)


def due_waves(waves: List[Dict[str, Any]], platform: str, round_num: int,
              minutes_per_round: int) -> List[Dict[str, Any]]:
    """Waves due for this platform in this round.

    Round ``round_num`` covers runner minutes
    [round_num * mpr, (round_num + 1) * mpr).
    """
    start = round_num * minutes_per_round
    end = start + minutes_per_round
    return [wave for wave in waves
            if wave["platform"] == platform
            and start <= wave["at_hour"] * 60 < end]


def record_injection(wave: Dict[str, Any], round_num: int,
                     db_path: str) -> Dict[str, Any]:
    """Resolve the injected post's id and remember the manifest entry.

    Identical content by the same author (deliberate message repetition)
    yields several DB rows over time — ids already claimed by earlier waves
    are excluded, so every manifest entry names only its own post(s).
    """
    post_ids: List[int] = []
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT post_id FROM post WHERE user_id = ? AND content = ?",
            (wave["poster_agent_id"], wave["content"]))
        post_ids = [row[0] for row in cur.fetchall()]
        conn.close()
    except Exception as exc:
        logger.warning("%s could not resolve post ids for %s: %s",
                       MARKER, wave["wave"], exc)
    claimed = {pid for entry in _injections for pid in entry["post_ids"]}
    post_ids = [pid for pid in post_ids if pid not in claimed]

    entry = {**wave, "round": round_num, "post_ids": post_ids}
    _injections.append(entry)
    print(f"{MARKER} injected {wave['wave']} on {wave['platform']} "
          f"round={round_num} post_ids={post_ids}")
    return entry


def write_manifest(simulation_dir: str) -> Optional[str]:
    """Write all injections of this run next to the databases. Never raises."""
    if not _injections:
        return None
    try:
        path = os.path.join(simulation_dir, "waves_manifest.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_injections, fh, ensure_ascii=False, indent=1)
        print(f"{MARKER} manifest: {path} ({len(_injections)} injections)")
        return path
    except Exception as exc:
        logger.warning("%s manifest write failed: %s", MARKER, exc)
        return None


def wave_stats() -> Dict[str, Any]:
    return {"injections": len(_injections),
            "waves": [{"wave": entry["wave"], "platform": entry["platform"],
                       "round": entry["round"],
                       "post_ids": list(entry["post_ids"])}
                      for entry in _injections]}
