"""Build the discriminating test set for the second PATCH-013 measurement.

12 private-person personas with an emotional stake in a polarizing consumer
topic (a ticket platform doubles its fees). Personas carry opinions and
lives, NOT register instructions - whether they write casually in the forum
and professionally on the business network is exactly what the archetype
prompt has to produce. Identical personas, seeds and config in both arms.
"""
import json
import os
import shutil

BASE = "/Users/miro_user/MiroFish-Offline/backend/uploads/simulations"

PERSONAS = [
    ("lena_k", "Lena Krause", "female", 19, "ENFP", "Germany", "student",
     "Lena is a broke literature student who goes to at least two concerts a month. Live music is the one thing she spends real money on, and the new fee hike means she can afford half as many shows. She is furious about it and not shy to say so."),
    ("tobi_gamer", "Tobias Brandt", "male", 24, "ISTP", "Germany", "warehouse logistics worker",
     "Tobias works shifts in a warehouse and unwinds at metal concerts. He has been burned twice by resale scams and thinks ticket platforms milk fans dry. He speaks bluntly and hates corporate excuses."),
    ("nadia_n", "Nadia Nowak", "female", 35, "ESFJ", "Poland", "pediatric nurse",
     "Nadia is a nurse and mother of two who saves for months to take her sister to a big pop concert once a year. The doubled fees feel like a slap in the face to her, and she worries ordinary families are being priced out of live culture."),
    ("kmueller58", "Klaus Mueller", "male", 58, "ISTJ", "Germany", "master electrician",
     "Klaus runs a small electrical business and has played bass in a hobby band for thirty years. He believes in fair prices for honest work and considers the new fees pure greed with no service behind it."),
    ("sophie_l", "Sophie Lindqvist", "female", 28, "INFJ", "Sweden", "primary school teacher",
     "Sophie is a teacher who organizes an annual concert trip for her choir friends. She is thoughtful and polite, but the fee increase genuinely angers her because it hits exactly the people who keep small venues alive."),
    ("marco_ultra", "Marco Ricci", "male", 31, "ESTP", "Italy", "car mechanic",
     "Marco follows his favourite band across half of Europe every summer. He is loud, emotional, quick to rant, and sees the fee doubling as a declaration of war on real fans."),
    ("annegret_h", "Annegret Hofmann", "female", 67, "ISFJ", "Germany", "retired librarian",
     "Annegret has subscribed to the philharmonic for forty years and recently started buying tickets online. She finds the new fees confusing and unfair, and she misses the times when a ticket price was simply the ticket price."),
    ("dennis_dj", "Dennis Vogel", "male", 22, "ENTP", "Austria", "apprentice baker and weekend DJ",
     "Dennis DJs at small club nights and buys dozens of cheap tickets a year to see other acts. The flat fee hits cheap tickets hardest, which he thinks is deliberately anti-scene, and he loves picking apart bad corporate arguments."),
    ("carla_fan", "Carla Duarte", "female", 42, "ESFP", "Spain", "hairdresser",
     "Carla's salon customers know her as the woman who has seen every big tour twice. Concerts are her identity and her escape; the fee hike makes her feel exploited by people who never stood in a front row."),
    ("piet_vk", "Piet van Kamp", "male", 47, "INTP", "Netherlands", "IT administrator",
     "Piet keeps a spreadsheet of every ticket he ever bought and can prove the fees rose faster than inflation. He argues with numbers, distrusts marketing language, and enjoys dismantling official statements line by line."),
    ("resi_m", "Theresa Maier", "female", 25, "ISFP", "Austria", "florist",
     "Theresa goes to small indie shows almost weekly. She fears the fee hike will kill exactly the small concerts she loves, because a 30 percent surcharge on a 15 euro ticket is brutal, and she wants people to notice that."),
    ("juergen_b", "Juergen Beck", "male", 39, "ENTJ", "Germany", "sales representative",
     "Juergen takes clients to concerts and buys family tickets for his kids' favourite bands. He understands margins and still finds the doubling indefensible; if a supplier of his tried that, he would drop them the same day."),
]

SEED_POSTS = [
    (0, "TicketWave just announced they are DOUBLING their service fee on every ticket starting next month. A 15 euro club show now costs 21 euro after fees. They call it 'enhanced customer experience investment'."),
    (3, "Did the math on my last ten concert tickets: I paid 62 euro in fees alone. For what exactly? An email with a QR code?"),
    (7, "TicketWave's press statement says the new fees 'reflect the true value of a seamless ticketing journey'. The concerts themselves get nothing from it."),
]


def personas_json():
    out = []
    for i, (username, name, gender, age, mbti, country, profession, persona) in enumerate(PERSONAS):
        out.append({
            "user_id": i,
            "username": username,
            "name": name,
            "realname": name,
            "bio": persona.split(".")[0] + ".",
            "persona": persona,
            "profession": profession,
            "gender": gender,
            "age": age,
            "mbti": mbti,
            "country": country,
            "interested_topics": ["live music", "concerts", "ticket prices"],
            "karma": 100,
        })
    return out


def agent_configs():
    cfgs = []
    for i in range(len(PERSONAS)):
        cfgs.append({
            "agent_id": i,
            "entity_name": PERSONAS[i][1],
            "entity_type": "person",
            "activity_level": 0.9,
            "active_hours": list(range(24)),
            "posts_per_hour": 0.5,
            "comments_per_hour": 1.0,
            "influence_weight": 1.0,
            "response_delay_min": 0,
            "response_delay_max": 2,
            "sentiment_bias": 0.0,
        })
    return cfgs


def config(sim_id, entry):
    return {
        "simulation_id": sim_id,
        "project_id": "polylop-arch013b",
        "graph_id": "handmade",
        "simulation_requirement": "Archetype A/B: room effect on register",
        "time_config": {
            "total_simulation_hours": 8,
            "minutes_per_round": 30,
            "agents_per_hour_min": 8,
            "agents_per_hour_max": 12,
            "peak_hours": list(range(24)),
            "off_peak_hours": [],
            "peak_activity_multiplier": 1.0,
            "off_peak_activity_multiplier": 1.0,
        },
        "agent_configs": agent_configs(),
        "event_config": {
            "initial_posts": [
                {"poster_agent_id": pid, "content": text}
                for pid, text in SEED_POSTS
            ],
        },
        "platforms": [entry],
        "llm_model": "",
        "llm_base_url": "",
    }


def main():
    profiles = personas_json()
    for arm, entry in (
            ("forum", {"name": "community", "archetype": "forum",
                       "posting_rate": True}),
            ("biz", {"name": "biznet", "archetype": "business_network",
                     "posting_rate": True})):
        for r in (1, 2, 3):
            d = os.path.join(BASE, f"sim_arch013b_{arm}_r{r}")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "reddit_profiles.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(profiles, fh, ensure_ascii=False, indent=1)
            with open(os.path.join(d, "simulation_config.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(config(f"sim_arch013b_{arm}_r{r}", entry), fh,
                          ensure_ascii=False, indent=1)
    print("6 arch013b dirs ready (12 private personas, polarizing topic)")


if __name__ == "__main__":
    main()
