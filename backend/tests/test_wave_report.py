"""
Deterministic checks for the Polylop wave report (PATCH-016).

Builds a fake simulation directory with hand-known numbers - manifest, db,
action log - and asserts the report reproduces them exactly. Covers the two
format quirks: the action log's 1-based rounds vs the manifest's 0-based
rounds, and JSON-parsed trace info (post_id 1 must not swallow post_id 10).

Run:  python backend/tests/test_wave_report.py
"""

import json
import os
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (_HERE, os.path.abspath(os.path.join(_HERE, "..", "scripts"))):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

from polylop_wave_report import wave_report  # noqa: E402


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    print(f"  ok - {description}")


tmp = tempfile.mkdtemp()
platform_dir = os.path.join(tmp, "community")
os.makedirs(platform_dir)

# Manifest: W1 -> post 1 (round 0), W2 -> post 10 (round 8).
# post 1 vs post 10 pins the JSON-parse requirement (no LIKE matching).
json.dump([
    {"wave": "W1", "platform": "community", "at_hour": 0,
     "poster_agent_id": 0, "content": "c1", "round": 0, "post_ids": [1]},
    {"wave": "W2", "platform": "community", "at_hour": 4,
     "poster_agent_id": 9, "content": "c2", "round": 8, "post_ids": [10]},
], open(os.path.join(tmp, "waves_manifest.json"), "w"))

# DB ground truth: W1 gets 2 comments + 2 likes + 1 dislike;
# W2 gets 1 comment + 1 like. Post 10's like must not leak into post 1.
conn = sqlite3.connect(os.path.join(tmp, "community_simulation.db"))
conn.execute("CREATE TABLE comment (comment_id INTEGER PRIMARY KEY, "
             "post_id INTEGER, content TEXT)")
conn.executemany("INSERT INTO comment VALUES (?, ?, ?)",
                 [(1, 1, "a"), (2, 1, "b"), (3, 10, "c"), (4, 99, "other")])
conn.execute("CREATE TABLE trace (user_id INTEGER, action TEXT, info TEXT)")
conn.executemany("INSERT INTO trace VALUES (?, ?, ?)", [
    (2, "like_post", '{"post_id": 1, "like_id": 1}'),
    (3, "like_post", '{"post_id": 1, "like_id": 2}'),
    (4, "like_post", '{"post_id": 10, "like_id": 3}'),
    (5, "dislike_post", '{"post_id": 1, "dislike_id": 1}'),
    (6, "like_post", '{"post_id": 99, "like_id": 4}'),
    (7, "like_post", 'kaputt'),
])
conn.commit()
conn.close()

# Action log (1-based rounds): W1 reactions in log rounds 2 and 5
# (= 0-based 1 and 4), W2 reaction in log round 10 (= 9). The CREATE_POST
# lines carry the wave post_ids and must not count as reactions.
with open(os.path.join(platform_dir, "actions.jsonl"), "w") as fh:
    for entry in [
        {"round": 1, "agent_id": 0, "action_type": "CREATE_POST",
         "action_args": {"content": "c1", "post_id": 1}},
        {"round": 2, "agent_id": 2, "action_type": "LIKE_POST",
         "action_args": {"post_id": 1}},
        {"round": 2, "agent_id": 3, "action_type": "CREATE_COMMENT",
         "action_args": {"post_id": 1, "content": "a"}},
        {"round": 5, "agent_id": 5, "action_type": "DISLIKE_POST",
         "action_args": {"post_id": 1}},
        {"round": 9, "agent_id": 9, "action_type": "CREATE_POST",
         "action_args": {"content": "c2", "post_id": 10}},
        {"round": 10, "agent_id": 4, "action_type": "LIKE_POST",
         "action_args": {"post_id": 10}},
        {"round": 10, "agent_id": 4, "action_type": "LIKE_POST",
         "action_args": {"post_id": 99}},
        {"event_type": "round_end", "round": 10, "actions_count": 2},
    ]:
        fh.write(json.dumps(entry) + "\n")

report = wave_report(tmp)
check(report is not None, "report produced")
waves = {w["wave"]: w for w in report["waves"]}

w1 = waves["W1"]
check(w1["comments"] == 2 and w1["likes"] == 2 and w1["dislikes"] == 1,
      "W1 db counts exact (post 10's like does not leak into post 1)")
check(w1["reactions_total"] == 5, "W1 reactions_total = 5")
check(w1["reactions_by_round"] == {"1": 2, "4": 1},
      "W1 round curve normalized to 0-based rounds, CREATE_POST excluded")
check(w1["first_reaction_round"] == 1 and w1["last_reaction_round"] == 4
      and w1["rounds_with_reactions"] == 2,
      "W1 first/last/active rounds")

w2 = waves["W2"]
check(w2["comments"] == 1 and w2["likes"] == 1 and w2["dislikes"] == 0,
      "W2 db counts exact")
check(w2["reactions_by_round"] == {"9": 1},
      "W2 curve has only its own like (post 99 ignored)")
check(w2["injected_round"] == 8 and w2["first_reaction_round"] == 9,
      "W2 reaction lands after its injection round")

check(os.path.exists(os.path.join(tmp, "waves_report.json")),
      "waves_report.json written")
check(wave_report(os.path.join(tmp, "community")) is None,
      "directory without manifest -> None (no-op)")

print()
print("all wave-report checks passed")
