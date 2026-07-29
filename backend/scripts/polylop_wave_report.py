"""
Polylop: per-wave report (PATCH-016, POL-SEQ01 step 2).

CUSTOM (Polylop): no upstream counterpart in MiroFish-Offline.

Turns the raw run data into per-wave Verlaufs-/Qualitäts-Kennzahlen — how a
campaign sendout lived in the conversation. Deliberately NO contact-dose or
reach promises (POL-SEQ01 §3: quantity claims belong to module C):

- direct reactions to the wave's post(s): comments, likes, dislikes
- reactions per round since injection, first/last reaction round, and the
  number of rounds with at least one reaction ("Lebensdauer im Gespräch")

Data sources and their quirks:
- waves_manifest.json  (PATCH-015: wave -> post_ids + injection round;
  rounds are 0-based runner rounds)
- <platform>_simulation.db  (comment table for replies; trace for
  likes/dislikes, info parsed as JSON — no LIKE-pattern matching, post_id 1
  must not match post_id 10)
- <platform>/actions.jsonl  (the only round-aligned record; the DB's
  created_at runs on OASIS' sandbox clock and does NOT match runner rounds).
  Log rounds are 1-based (round_num + 1) — everything here is normalized to
  the manifest's 0-based rounds.

Runs after the simulation loop in the generic runner (only when waves were
configured), and works post-hoc on any finished run:

    python polylop_wave_report.py <simulation_dir>
"""

import json
import logging
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("polylop.wave_report")

MARKER = "POLYLOP-WAVE-REPORT"

# direct reactions to a post; the wave's own CREATE_POST carries the same
# post_id in the action log and must never count as a reaction
REACTION_TYPES = {"like_post", "dislike_post", "create_comment"}


def _comment_posts(db_path: str) -> Dict[int, int]:
    """comment_id -> post_id. The action log's CREATE_COMMENT entries carry
    only a comment_id (measured on sim_arch015_waves, 2026-07-27) - the post
    they belong to has to come from the database."""
    mapping: Dict[int, int] = {}
    if not os.path.exists(db_path):
        return mapping
    conn = sqlite3.connect(db_path)
    try:
        for comment_id, post_id in conn.execute(
                "SELECT comment_id, post_id FROM comment"):
            mapping[comment_id] = post_id
    finally:
        conn.close()
    return mapping


def _reaction_rounds(actions_path: str, post_to_wave: Dict[int, str],
                     comment_to_post: Dict[int, int]) -> Dict[str, Dict[int, int]]:
    """wave -> {0-based round -> reaction count}, from the action log."""
    rounds: Dict[str, Dict[int, int]] = {}
    if not os.path.exists(actions_path):
        logger.warning("%s no action log at %s - round curves unavailable",
                       MARKER, actions_path)
        return rounds
    with open(actions_path, "r", encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            action = str(entry.get("action_type") or "").lower()
            if action not in REACTION_TYPES:
                continue
            args = entry.get("action_args") or {}
            post_id = args.get("post_id")
            if post_id is None and action == "create_comment":
                post_id = comment_to_post.get(args.get("comment_id"))
            wave = post_to_wave.get(post_id)
            if wave is None or "round" not in entry:
                continue
            log_round = int(entry["round"]) - 1  # log is 1-based
            per_wave = rounds.setdefault(wave, {})
            per_wave[log_round] = per_wave.get(log_round, 0) + 1
    return rounds


def _db_counts(db_path: str, post_ids: List[int]) -> Dict[str, int]:
    """Ground-truth reaction counts from the platform database."""
    counts = {"comments": 0, "likes": 0, "dislikes": 0}
    if not post_ids or not os.path.exists(db_path):
        return counts
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        placeholders = ", ".join("?" for _ in post_ids)
        counts["comments"] = cur.execute(
            f"SELECT COUNT(*) FROM comment WHERE post_id IN ({placeholders})",
            post_ids).fetchone()[0]
        wanted = set(post_ids)
        for action, key in (("like_post", "likes"),
                            ("dislike_post", "dislikes")):
            for (info,) in cur.execute(
                    "SELECT info FROM trace WHERE action = ?", (action,)):
                try:
                    if json.loads(info).get("post_id") in wanted:
                        counts[key] += 1
                except (json.JSONDecodeError, TypeError):
                    continue
    finally:
        conn.close()
    return counts


def wave_report(simulation_dir: str) -> Optional[Dict[str, Any]]:
    """Compute and write waves_report.json. Returns the report, or None when
    the run had no waves. Never raises out of the runner."""
    manifest_path = os.path.join(simulation_dir, "waves_manifest.json")
    if not os.path.exists(manifest_path):
        return None
    try:
        manifest = json.load(open(manifest_path, encoding="utf-8"))

        report_waves = []
        by_platform: Dict[str, List[Dict[str, Any]]] = {}
        for entry in manifest:
            by_platform.setdefault(entry["platform"], []).append(entry)

        for platform, entries in by_platform.items():
            post_to_wave = {pid: e["wave"] for e in entries
                           for pid in e["post_ids"]}
            db_path = os.path.join(simulation_dir,
                                   f"{platform}_simulation.db")
            rounds = _reaction_rounds(
                os.path.join(simulation_dir, platform, "actions.jsonl"),
                post_to_wave, _comment_posts(db_path))
            for e in entries:
                counts = _db_counts(db_path, e["post_ids"])
                curve = rounds.get(e["wave"], {})
                reaction_rounds = sorted(curve)
                report_waves.append({
                    "wave": e["wave"],
                    "platform": platform,
                    "injected_round": e["round"],
                    "post_ids": e["post_ids"],
                    **counts,
                    "reactions_total": sum(counts.values()),
                    "reactions_by_round": {str(r): curve[r]
                                           for r in reaction_rounds},
                    "first_reaction_round": (reaction_rounds[0]
                                             if reaction_rounds else None),
                    "last_reaction_round": (reaction_rounds[-1]
                                            if reaction_rounds else None),
                    "rounds_with_reactions": len(reaction_rounds),
                })

        report = {"waves": report_waves}
        out_path = os.path.join(simulation_dir, "waves_report.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)

        for w in report_waves:
            print(f"{MARKER} {w['wave']} ({w['platform']}, Runde "
                  f"{w['injected_round']}): {w['comments']} Kommentare, "
                  f"{w['likes']} Likes, {w['dislikes']} Dislikes - "
                  f"aktiv in {w['rounds_with_reactions']} Runden "
                  f"({w['first_reaction_round']}-{w['last_reaction_round']})")
        print(f"{MARKER} geschrieben: {out_path}")
        return report
    except Exception as exc:
        logger.warning("%s failed: %s", MARKER, exc)
        print(f"{MARKER}-FAILED {exc}")
        return None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python polylop_wave_report.py <simulation_dir>")
        sys.exit(1)
    if wave_report(sys.argv[1]) is None:
        print(f"{MARKER} no waves_manifest.json in {sys.argv[1]}")
        sys.exit(1)
