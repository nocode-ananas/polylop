"""PATCH-014 measurement: newsletter vs forum, identical personas.

Structural claim: on the newsletter, only the sender can publish - reader
post count must be exactly 0 (enforced by tool subsets). Plus reaction
metrics per issue. Forum reference: the sim_arch013b_forum_* runs (same 12
personas; note the differences honestly: forum seeds were authored by three
agents and posting_rate was on there, off here - irrelevant for the
structural claim, stated for the reach numbers).
"""
import json
import sqlite3
import statistics

BASE = "/Users/miro_user/MiroFish-Offline/backend/uploads/simulations"
SENDERS = {9}


def words(text):
    return len((text or "").split())


for r in (1, 2, 3):
    sim = f"sim_arch014_news_r{r}"
    conn = sqlite3.connect(f"{BASE}/{sim}/letter_simulation.db")
    cur = conn.cursor()

    posts = list(cur.execute("SELECT post_id, user_id, content FROM post"))
    post_authors = {row[1] for row in posts}
    comments = list(cur.execute("SELECT post_id, user_id, content FROM comment"))
    likes = list(cur.execute(
        "SELECT user_id FROM trace WHERE action = 'like_post'"))

    reader_posts = [p for p in posts if p[1] not in SENDERS]
    per_issue = {}
    for pid, uid, _ in comments:
        per_issue[pid] = per_issue.get(pid, 0) + 1

    report = json.load(open(f"{BASE}/{sim}/polylop_run_report.json"))
    p = report["platforms"]["letter"]

    print(f"=== {sim} ===")
    print(f"  agent_steps={p['agent_steps']} actions={p['actions_with_effect']}"
          f" rejected={report['llm_requests']['rejected_3240'] + report['llm_requests']['rejected_other_400']}"
          f" warnings={report['warnings']}")
    print(f"  posts={len(posts)} post_authors={sorted(post_authors)}"
          f" READER_POSTS={len(reader_posts)}")
    print(f"  replies={len(comments)} likes={len(likes)}"
          f" replies_per_issue={dict(sorted(per_issue.items()))}")
    if comments:
        reply_words = [words(c[2]) for c in comments]
        print(f"  reply_mean_words={round(statistics.mean(reply_words), 1)}")
        for _, uid, text in comments[:2]:
            print(f"  sample reply (agent {uid}): {(text or '')[:170]}")
    conn.close()

print()
print("Forum-Referenz (sim_arch013b_forum_r1..3): Nicht-Seed-Posts je Lauf "
      "26-37, Post-Autoren: praktisch alle 12 Agenten (posting_rate an).")
