"""
Deterministic checks for the Polylop wave module (PATCH-015).

Pure functions plus a throwaway sqlite file - no oasis, no LLM, no network.

Run:  python backend/tests/test_waves.py
"""

import os
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (_HERE, os.path.abspath(os.path.join(_HERE, "..", "scripts"))):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

import polylop_waves as pw  # noqa: E402


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    print(f"  ok - {description}")


def expect_raise(config, platforms, description):
    try:
        pw.parse_waves(config, platforms)
    except ValueError:
        print(f"  ok - {description}")
    else:
        raise AssertionError(f"accepted: {description}")


print("parse_waves:")
check(pw.parse_waves({}, ["a"]) == [], "no waves key -> empty list")
check(pw.parse_waves({"waves": []}, ["a"]) == [], "empty waves -> empty list")

waves = pw.parse_waves({"waves": [
    {"at_hour": 0, "platform": "biznet", "poster_agent_id": 9, "content": "x"},
    {"at_hour": 4.5, "platform": "letter", "poster_agent_id": "3",
     "content": "y", "wave": "Follow-up"},
]}, ["biznet", "letter"])
check(waves[0]["wave"] == "W1" and waves[1]["wave"] == "Follow-up",
      "default names W<i>, explicit names kept")
check(waves[1]["poster_agent_id"] == 3 and waves[1]["at_hour"] == 4.5,
      "types normalized (int poster, float hour)")

expect_raise({"waves": [{"at_hour": 1, "platform": "nope",
                         "poster_agent_id": 0, "content": "x"}]},
             ["biznet"], "unknown platform raises")
expect_raise({"waves": [{"platform": "biznet", "poster_agent_id": 0,
                         "content": "x"}]},
             ["biznet"], "missing at_hour raises")
expect_raise({"waves": [{"at_hour": -1, "platform": "biznet",
                         "poster_agent_id": 0, "content": "x"}]},
             ["biznet"], "negative at_hour raises")
expect_raise({"waves": [{"at_hour": 1, "platform": "biznet",
                         "poster_agent_id": 0, "content": "  "}]},
             ["biznet"], "empty content raises")
expect_raise({"waves": [{"at_hour": 1, "platform": "biznet",
                         "content": "x"}]},
             ["biznet"], "missing poster_agent_id raises")

print("due_waves:")
schedule = pw.parse_waves({"waves": [
    {"at_hour": 0, "platform": "a", "poster_agent_id": 0, "content": "w1"},
    {"at_hour": 0.5, "platform": "a", "poster_agent_id": 0, "content": "w2"},
    {"at_hour": 4, "platform": "a", "poster_agent_id": 0, "content": "w3"},
    {"at_hour": 4, "platform": "b", "poster_agent_id": 0, "content": "w4"},
]}, ["a", "b"])
check([w["wave"] for w in pw.due_waves(schedule, "a", 0, 30)] == ["W1"],
      "at_hour 0 fires in round 0")
check([w["wave"] for w in pw.due_waves(schedule, "a", 1, 30)] == ["W2"],
      "at_hour 0.5 with 30-min rounds fires in round 1, not round 0")
check([w["wave"] for w in pw.due_waves(schedule, "a", 8, 30)] == ["W3"],
      "at_hour 4 fires in round 8 (240 min / 30)")
check(pw.due_waves(schedule, "b", 8, 30)[0]["wave"] == "W4"
      and pw.due_waves(schedule, "b", 0, 30) == [],
      "platform filter holds")
check(pw.due_waves(schedule, "a", 4, 60)[0]["wave"] == "W3",
      "60-min rounds: at_hour 4 fires in round 4")

print("check_horizon:")
pw.check_horizon(schedule, 2)  # prints warnings for W3/W4, must not raise
check(True, "horizon check never raises")

print("record_injection + manifest:")
tmpdir = tempfile.mkdtemp()
db = os.path.join(tmpdir, "a_simulation.db")
conn = sqlite3.connect(db)
conn.execute("CREATE TABLE post (post_id INTEGER PRIMARY KEY, "
             "user_id INTEGER, content TEXT)")
conn.execute("INSERT INTO post VALUES (1, 0, 'repeat me')")
conn.execute("INSERT INTO post VALUES (2, 5, 'other')")
conn.commit()

wave_a = {"wave": "W1", "platform": "a", "at_hour": 0,
          "poster_agent_id": 0, "content": "repeat me"}
entry1 = pw.record_injection(wave_a, 0, db)
check(entry1["post_ids"] == [1], "post id resolved by author+content")

conn.execute("INSERT INTO post VALUES (3, 0, 'repeat me')")
conn.commit()
entry2 = pw.record_injection({**wave_a, "wave": "W2"}, 8, db)
check(entry2["post_ids"] == [3],
      "identical repeated content: second wave claims only the new post id")

path = pw.write_manifest(tmpdir)
check(path is not None and os.path.exists(path), "manifest written")
import json  # noqa: E402
manifest = json.load(open(path))
check(len(manifest) == 2 and manifest[1]["round"] == 8,
      "manifest carries both injections with rounds")
conn.close()

print()
print("all wave checks passed")
