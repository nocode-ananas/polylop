"""
Polylop: campaign driver — the Achse-2 workflow (PATCH-017, POL-SEQ01 step 3).

CUSTOM (Polylop): no upstream counterpart in MiroFish-Offline.

Runs a campaign definition as variant x repetition, pools the per-wave
reports and states RANGES over the repetitions — never single-run effect
claims (POL-KALIB01). This turns the hand-driven workflow of the archetype
measurements into a building block.

Campaign definition (JSON):

    {
      "campaign_id": "ticketwave",
      "template_dir": "uploads/simulations/sim_arch013b_forum_r1",
      "repetitions": 3,
      "variants": [
        {"name": "eine-welle",  "waves": [ ...PATCH-015 wave entries... ]},
        {"name": "zwei-wellen", "waves": [ ... ]}
      ]
    }

Per variant and repetition the driver prepares a run directory
(sim_camp_<id>_<variant>_r<n>: template profiles copied, config with the
variant's waves and its own simulation_id), runs the generic runner
sequentially, and afterwards pools every run's waves_report.json into
uploads/simulations/campaign_<id>/campaign_report.json.

The template's initial_posts stay untouched on purpose: they are ambient
world content the campaign runs into; wave attribution is per post id and
unaffected. A variant with an empty waves list is a valid baseline arm.

Usage (inside the container, from /app/backend):

    .venv/bin/python scripts/run_campaign.py --campaign <definition.json>
        [--max-rounds N]
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("polylop.campaign")

MARKER = "POLYLOP-CAMPAIGN"

PROFILE_FILES = ("reddit_profiles.json", "twitter_profiles.csv")

# pooled per wave, ranges over repetitions
POOL_METRICS = ("reactions_total", "comments", "likes", "dislikes",
                "rounds_with_reactions", "first_reaction_round",
                "last_reaction_round")


def load_campaign(path: str) -> Dict[str, Any]:
    """Read and validate a campaign definition. Loud on every defect."""
    with open(path, encoding="utf-8") as fh:
        campaign = json.load(fh)

    campaign_id = campaign.get("campaign_id")
    if not campaign_id or not str(campaign_id).strip():
        raise ValueError(f"{MARKER} campaign_id missing")
    template_dir = campaign.get("template_dir")
    if not template_dir or not os.path.isdir(template_dir):
        raise ValueError(f"{MARKER} template_dir missing or not a directory: "
                         f"{template_dir!r}")
    if not os.path.exists(os.path.join(template_dir, "simulation_config.json")):
        raise ValueError(f"{MARKER} template_dir has no simulation_config.json")
    if not any(os.path.exists(os.path.join(template_dir, p))
               for p in PROFILE_FILES):
        raise ValueError(f"{MARKER} template_dir has no profile file "
                         f"({' / '.join(PROFILE_FILES)})")
    try:
        repetitions = int(campaign.get("repetitions"))
    except (TypeError, ValueError):
        raise ValueError(f"{MARKER} repetitions missing or not an integer")
    if repetitions < 1:
        raise ValueError(f"{MARKER} repetitions must be >= 1")
    variants = campaign.get("variants")
    if not variants:
        raise ValueError(f"{MARKER} no variants")
    seen = set()
    for variant in variants:
        name = (variant or {}).get("name")
        if not name or name in seen:
            raise ValueError(f"{MARKER} every variant needs a unique name: "
                             f"{variant!r}")
        seen.add(name)
        if not isinstance(variant.get("waves"), list):
            raise ValueError(f"{MARKER} variant {name!r}: waves must be a "
                             "list (empty list = baseline arm)")
    return campaign


def prepare_run_dir(campaign: Dict[str, Any], variant: Dict[str, Any],
                    rep: int, runs_root: str) -> str:
    """Create the run directory for one variant repetition."""
    sim_id = f"sim_camp_{campaign['campaign_id']}_{variant['name']}_r{rep}"
    run_dir = os.path.join(runs_root, sim_id)
    os.makedirs(run_dir, exist_ok=True)

    for profile in PROFILE_FILES:
        src = os.path.join(campaign["template_dir"], profile)
        if os.path.exists(src):
            shutil.copy(src, run_dir)

    config = json.load(open(
        os.path.join(campaign["template_dir"], "simulation_config.json"),
        encoding="utf-8"))
    config["simulation_id"] = sim_id
    config["waves"] = variant["waves"]
    with open(os.path.join(run_dir, "simulation_config.json"), "w",
              encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=1)
    return run_dir


def run_one(run_dir: str, max_rounds: Optional[int] = None) -> None:
    """Run the generic runner for one prepared directory. Loud on failure."""
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "run_parallel_simulation.py")
    cmd = [sys.executable, runner,
           "--config", os.path.join(run_dir, "simulation_config.json"),
           "--no-wait"]
    if max_rounds:
        cmd += ["--max-rounds", str(max_rounds)]
    print(f"{MARKER} run: {os.path.basename(run_dir)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"{MARKER} runner failed for {run_dir} "
                           f"(exit {result.returncode})")


def _ranges(values: List[Any]) -> Dict[str, Any]:
    numeric = [v for v in values if isinstance(v, (int, float))]
    return {
        "values": values,
        "min": min(numeric) if numeric else None,
        "max": max(numeric) if numeric else None,
    }


def pool(campaign: Dict[str, Any],
         run_dirs: Dict[str, List[str]]) -> Dict[str, Any]:
    """Pool the runs' wave reports into per-variant ranges."""
    variants_out = []
    for variant in campaign["variants"]:
        name = variant["name"]
        runs = []
        for run_dir in run_dirs.get(name, []):
            entry = {"run": os.path.basename(run_dir), "waves": [],
                     "reactions_sum": 0, "agent_steps": None,
                     "rejected": None}
            wr_path = os.path.join(run_dir, "waves_report.json")
            if os.path.exists(wr_path):
                waves = json.load(open(wr_path, encoding="utf-8"))["waves"]
                entry["waves"] = waves
                entry["reactions_sum"] = sum(w["reactions_total"]
                                             for w in waves)
            rr_path = os.path.join(run_dir, "polylop_run_report.json")
            if os.path.exists(rr_path):
                run_report = json.load(open(rr_path, encoding="utf-8"))
                platforms = run_report.get("platforms") or {}
                entry["agent_steps"] = sum(p.get("agent_steps", 0)
                                           for p in platforms.values())
                requests = run_report.get("llm_requests") or {}
                entry["rejected"] = (requests.get("rejected_3240", 0)
                                     + requests.get("rejected_other_400", 0))
            runs.append(entry)

        wave_names = [w["wave"] if isinstance(w, dict) and w.get("wave")
                      else f"W{i + 1}"
                      for i, w in enumerate(variant["waves"])]
        pooled_waves = {}
        for wave_name in wave_names:
            per_metric = {}
            for metric in POOL_METRICS:
                values = []
                for run in runs:
                    match = next((w for w in run["waves"]
                                  if w["wave"] == wave_name), None)
                    values.append(match.get(metric) if match else None)
                per_metric[metric] = _ranges(values)
            pooled_waves[wave_name] = per_metric

        variants_out.append({
            "variant": name,
            "runs": runs,
            "reactions_sum": _ranges([r["reactions_sum"] for r in runs]),
            "waves": pooled_waves,
        })
    return {
        "campaign_id": campaign["campaign_id"],
        "repetitions": campaign["repetitions"],
        "template_dir": campaign["template_dir"],
        "note": ("Bandbreiten ueber Wiederholungslaeufe, keine "
                 "Einzellauf-Aussagen (POL-KALIB01); Verlaufs-/"
                 "Qualitaets-Kennzahlen, keine Kontakt-Dosen (POL-SEQ01)."),
        "variants": variants_out,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Polylop campaign driver")
    parser.add_argument("--campaign", required=True,
                        help="campaign definition (JSON)")
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--runs-root", default="uploads/simulations",
                        help="where run directories are created")
    args = parser.parse_args()

    campaign = load_campaign(args.campaign)

    run_dirs: Dict[str, List[str]] = {}
    for variant in campaign["variants"]:
        for rep in range(1, campaign["repetitions"] + 1):
            run_dir = prepare_run_dir(campaign, variant, rep, args.runs_root)
            run_one(run_dir, args.max_rounds)
            run_dirs.setdefault(variant["name"], []).append(run_dir)

    report = pool(campaign, run_dirs)
    out_dir = os.path.join(args.runs_root,
                           f"campaign_{campaign['campaign_id']}")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "campaign_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print(f"{MARKER} ==== {campaign['campaign_id']}: "
          f"{len(campaign['variants'])} Varianten x "
          f"{campaign['repetitions']} Wiederholungen ====")
    for variant in report["variants"]:
        rs = variant["reactions_sum"]
        print(f"{MARKER} {variant['variant']}: Reaktionen gesamt "
              f"{rs['min']}-{rs['max']} (je Lauf: {rs['values']})")
        for wave_name, metrics in variant["waves"].items():
            rt = metrics["reactions_total"]
            print(f"{MARKER}   {wave_name}: Reaktionen {rt['min']}-{rt['max']}"
                  f" (je Lauf: {rt['values']})")
    print(f"{MARKER} Report: {out_path}")


if __name__ == "__main__":
    main()
