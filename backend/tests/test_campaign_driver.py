"""
Deterministic checks for the Polylop campaign driver (PATCH-017).

Covers the pure parts — definition validation, run-dir preparation, pooling
math on hand-built wave reports. The subprocess path is exercised by the
end-to-end campaign run (changelog), not here.

Run:  python backend/tests/test_campaign_driver.py
"""

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (_HERE, os.path.abspath(os.path.join(_HERE, "..", "scripts"))):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

from run_campaign import load_campaign, pool, prepare_run_dir  # noqa: E402


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    print(f"  ok - {description}")


def expect_raise(campaign_dict, description):
    tmp_path = os.path.join(tempfile.mkdtemp(), "campaign.json")
    json.dump(campaign_dict, open(tmp_path, "w"))
    try:
        load_campaign(tmp_path)
    except ValueError:
        print(f"  ok - {description}")
    else:
        raise AssertionError(f"accepted: {description}")


# --- fixture: template dir --------------------------------------------------
template = tempfile.mkdtemp()
json.dump({"simulation_id": "template", "agent_configs": [],
           "event_config": {"initial_posts": [{"poster_agent_id": 0,
                                               "content": "ambient"}]},
           "platforms": [{"name": "community", "archetype": "forum"}]},
          open(os.path.join(template, "simulation_config.json"), "w"))
json.dump([{"username": "u0"}],
          open(os.path.join(template, "reddit_profiles.json"), "w"))

WAVE = {"at_hour": 0, "platform": "community", "poster_agent_id": 0,
        "content": "w", "wave": "W1"}

print("load_campaign:")
good_path = os.path.join(tempfile.mkdtemp(), "campaign.json")
json.dump({"campaign_id": "test", "template_dir": template, "repetitions": 2,
           "variants": [{"name": "A", "waves": [WAVE]},
                        {"name": "B", "waves": []}]},
          open(good_path, "w"))
campaign = load_campaign(good_path)
check(campaign["campaign_id"] == "test", "valid definition loads")

expect_raise({"template_dir": template, "repetitions": 1,
              "variants": [{"name": "A", "waves": []}]},
             "missing campaign_id raises")
expect_raise({"campaign_id": "x", "template_dir": "/nope", "repetitions": 1,
              "variants": [{"name": "A", "waves": []}]},
             "missing template dir raises")
expect_raise({"campaign_id": "x", "template_dir": template, "repetitions": 0,
              "variants": [{"name": "A", "waves": []}]},
             "repetitions < 1 raises")
expect_raise({"campaign_id": "x", "template_dir": template, "repetitions": 1,
              "variants": []}, "no variants raises")
expect_raise({"campaign_id": "x", "template_dir": template, "repetitions": 1,
              "variants": [{"name": "A", "waves": []},
                           {"name": "A", "waves": []}]},
             "duplicate variant name raises")
expect_raise({"campaign_id": "x", "template_dir": template, "repetitions": 1,
              "variants": [{"name": "A"}]},
             "variant without waves list raises")

print("prepare_run_dir:")
runs_root = tempfile.mkdtemp()
run_dir = prepare_run_dir(campaign, campaign["variants"][0], 1, runs_root)
check(os.path.basename(run_dir) == "sim_camp_test_A_r1",
      "run dir named sim_camp_<id>_<variant>_r<rep>")
prepared = json.load(open(os.path.join(run_dir, "simulation_config.json")))
check(prepared["simulation_id"] == "sim_camp_test_A_r1",
      "simulation_id set per run")
check(prepared["waves"] == [WAVE], "variant waves injected")
check(prepared["event_config"]["initial_posts"][0]["content"] == "ambient",
      "template initial_posts kept (ambient world content)")
check(os.path.exists(os.path.join(run_dir, "reddit_profiles.json")),
      "profiles copied")

baseline_dir = prepare_run_dir(campaign, campaign["variants"][1], 2, runs_root)
check(json.load(open(os.path.join(
    baseline_dir, "simulation_config.json")))["waves"] == [],
      "baseline arm gets an empty waves list")

print("pool:")


def fake_run(run_dir, wave_reactions, steps):
    os.makedirs(run_dir, exist_ok=True)
    json.dump({"waves": [
        {"wave": name, "platform": "community", "injected_round": 0,
         "post_ids": [1], "comments": c, "likes": l, "dislikes": 0,
         "reactions_total": c + l, "reactions_by_round": {},
         "first_reaction_round": fr, "last_reaction_round": fr,
         "rounds_with_reactions": 1 if fr is not None else 0}
        for name, (c, l, fr) in wave_reactions.items()]},
        open(os.path.join(run_dir, "waves_report.json"), "w"))
    json.dump({"platforms": {"community": {"agent_steps": steps}},
               "llm_requests": {"rejected_3240": 0, "rejected_other_400": 0}},
              open(os.path.join(run_dir, "polylop_run_report.json"), "w"))


pool_root = tempfile.mkdtemp()
r1 = os.path.join(pool_root, "sim_camp_test_A_r1")
r2 = os.path.join(pool_root, "sim_camp_test_A_r2")
fake_run(r1, {"W1": (3, 1, 1)}, 100)      # 4 reactions
fake_run(r2, {"W1": (5, 2, None)}, 120)   # 7 reactions

report = pool(campaign, {"A": [r1, r2], "B": []})
variant_a = report["variants"][0]
check(variant_a["reactions_sum"]["values"] == [4, 7]
      and variant_a["reactions_sum"]["min"] == 4
      and variant_a["reactions_sum"]["max"] == 7,
      "per-run reaction sums pooled as range 4-7")
w1 = variant_a["waves"]["W1"]
check(w1["comments"]["min"] == 3 and w1["comments"]["max"] == 5,
      "wave metric range exact (comments 3-5)")
check(w1["first_reaction_round"]["values"] == [1, None]
      and w1["first_reaction_round"]["min"] == 1,
      "None values listed transparently, range over numeric only")
check(report["variants"][1]["runs"] == [],
      "variant without runs pools empty, no crash")
check("Bandbreiten" in report["note"], "report carries the methods note")

print()
print("all campaign-driver checks passed")
