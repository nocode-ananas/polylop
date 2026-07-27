"""PATCH-013 A/B analysis: forum vs business_network, identical personas.

Reads the six measurement runs' sqlite DBs and reports structural and
textual behaviour metrics per run, then per-arm ranges. No effect claims
beyond what three runs per arm support (POL-KALIB01: ranges, not point
estimates).
"""
import json
import re
import sqlite3
import statistics
import sys

BASE = "/Users/miro_user/MiroFish-Offline/backend/uploads/simulations"
SERIES = sys.argv[1] if len(sys.argv) > 1 else "sim_arch013"
ARMS = {
    "forum": [(f"{SERIES}_forum_r{r}", "community") for r in (1, 2, 3)],
    "business": [(f"{SERIES}_biz_r{r}", "biznet") for r in (1, 2, 3)],
}
SEEDS = 5 if SERIES == "sim_arch013" else 3

LLM_ACTIONS = ("create_post", "create_comment", "like_post", "like_comment",
               "dislike_post", "dislike_comment", "repost", "quote_post",
               "follow", "mute", "do_nothing", "search_posts", "search_user",
               "trend")

EXCLAM = re.compile(r"!")
FIRST_PERSON_PRO = re.compile(
    r"\b(in my experience|from my perspective|professionally|"
    r"in our industry|as a\b|best practice|insight|expertise)", re.I)
CASUAL = re.compile(
    r"\b(lol|omg|wtf|haha|awesome!!|so cool|guys)\b|!!|\?\?|😂|🔥|❤️", re.I)
ANGRY = re.compile(
    r"\b(scam|rip.?off|greedy|greed|ridiculous|outrageous|disgusting|"
    r"shameless|insult|slap in the face|milk(ing)? (us|fans)|"
    r"daylight robbery|joke|absurd|furious)\b", re.I)


def words(text):
    return len((text or "").split())


def analyze_run(sim_dir, platform):
    db = f"{BASE}/{sim_dir}/{platform}_simulation.db"
    conn = sqlite3.connect(db)
    cur = conn.cursor()

    counts = {}
    for (action,) in cur.execute("SELECT action FROM trace"):
        counts[action] = counts.get(action, 0) + 1
    mix = {a: counts.get(a, 0) for a in LLM_ACTIONS if counts.get(a, 0)}

    comments = [row[0] for row in cur.execute("SELECT content FROM comment")]
    # exclude the seed posts (they are identical config input in both arms):
    # seeds are the first N posts created in round 0 by ManualAction. We
    # approximate by dropping the 5 earliest post rows (config has 5
    # placeable seed posts, verified in the run logs).
    posts = [row[0] for row in cur.execute(
        "SELECT content FROM post ORDER BY post_id")][SEEDS:]

    def text_stats(texts):
        if not texts:
            return {"n": 0}
        wordcounts = [words(t) for t in texts]
        return {
            "n": len(texts),
            "mean_words": round(statistics.mean(wordcounts), 1),
            "exclam_per_text": round(
                sum(len(EXCLAM.findall(t or "")) for t in texts) / len(texts), 2),
            "professional_marker_share": round(
                sum(1 for t in texts if FIRST_PERSON_PRO.search(t or "")) / len(texts), 2),
            "casual_marker_share": round(
                sum(1 for t in texts if CASUAL.search(t or "")) / len(texts), 2),
            "angry_marker_share": round(
                sum(1 for t in texts if ANGRY.search(t or "")) / len(texts), 2),
        }

    report = json.load(open(f"{BASE}/{sim_dir}/polylop_run_report.json"))
    pdata = report["platforms"][platform]

    conn.close()
    return {
        "run": sim_dir,
        "agent_steps": pdata["agent_steps"],
        "actions_with_effect": pdata["actions_with_effect"],
        "rejected": (report["llm_requests"]["rejected_3240"]
                     + report["llm_requests"]["rejected_other_400"]),
        "mix": mix,
        "comments": text_stats(comments),
        "posts": text_stats(posts),
        "comment_samples": comments[:3],
    }


def per_arm(results):
    def collect(path):
        vals = []
        for r in results:
            v = r
            for key in path:
                v = v.get(key) if isinstance(v, dict) else None
                if v is None:
                    break
            if isinstance(v, (int, float)):
                vals.append(v)
        return vals

    def rng(vals):
        return f"{min(vals)}-{max(vals)}" if vals else "-"

    dislikes = [sum(v for a, v in r["mix"].items() if a.startswith("dislike"))
                for r in results]
    shares = [sum(v for a, v in r["mix"].items() if a in ("repost", "quote_post"))
              for r in results]
    return {
        "agent_steps": rng(collect(["agent_steps"])),
        "actions": rng(collect(["actions_with_effect"])),
        "dislike_actions": rng(dislikes),
        "share_actions (repost+quote)": rng(shares),
        "comments_n": rng(collect(["comments", "n"])),
        "comment_mean_words": rng(collect(["comments", "mean_words"])),
        "comment_exclam": rng(collect(["comments", "exclam_per_text"])),
        "comment_professional_share": rng(collect(["comments", "professional_marker_share"])),
        "comment_casual_share": rng(collect(["comments", "casual_marker_share"])),
        "comment_angry_share": rng(collect(["comments", "angry_marker_share"])),
        "post_angry_share": rng(collect(["posts", "angry_marker_share"])),
        "posts_n": rng(collect(["posts", "n"])),
        "post_mean_words": rng(collect(["posts", "mean_words"])),
    }


def main():
    out = {}
    for arm, runs in ARMS.items():
        results = []
        for sim_dir, platform in runs:
            try:
                results.append(analyze_run(sim_dir, platform))
            except Exception as exc:
                print(f"SKIP {sim_dir}: {exc}", file=sys.stderr)
        out[arm] = {"runs": results, "summary": per_arm(results)}

    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
