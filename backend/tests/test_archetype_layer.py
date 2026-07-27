"""
Deterministic checks for the Polylop archetype layer (PATCH-011).

No LLM, no network. Runs against the real installed oasis package. Every case
executes in a fresh subprocess so monkey-patches from one case cannot leak
into the next. PATCH-011 is a refactor that must not change behaviour — most
cases assert exactly that.

Run inside the container:

    /app/backend/.venv/bin/python backend/tests/test_archetype_layer.py
"""

import asyncio
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for candidate in (_HERE, os.path.abspath(os.path.join(_HERE, "..", "scripts"))):
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

REDDIT_KWARGS = dict(recsys_type="reddit", allow_self_rating=True,
                     show_score=True, max_rec_post_len=100,
                     refresh_rec_post_count=5)
TWITTER_KWARGS = dict(recsys_type="twhin-bert", refresh_rec_post_count=2,
                      max_rec_post_len=2, following_post_count=3)


def _platform(**kwargs):
    from oasis.social_platform.platform import Platform
    return Platform(db_path=":memory:", channel=object(), **kwargs)


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    print(f"  ok - {description}")


def case_classify():
    """DefaultPlatformType-shaped instances get the right archetype name."""
    import polylop_archetypes as arch
    arch.apply_archetypes({})
    reddit = _platform(**REDDIT_KWARGS)
    twitter = _platform(**TWITTER_KWARGS)
    unknown = _platform(recsys_type="random")
    check(arch.archetype_of(reddit) == "forum", "reddit recsys -> forum")
    check(arch.archetype_of(twitter) == "micro_broadcast",
          "twhin recsys -> micro_broadcast")
    check(arch.archetype_of(unknown) is None, "random recsys -> unclassified")
    stats = arch.archetype_stats()
    check(len(stats["instances"]) == 3, "all three instances registered")


def case_noop():
    """No feed_slots configured -> nothing changes anywhere (regression)."""
    import polylop_archetypes as arch
    import polylop_feed_capacity as cap
    config = {"reddit_config": {"platform": "reddit"},
              "twitter_config": {"platform": "twitter"}}
    arch.apply_archetypes(config)
    check(cap.apply_feed_capacity(config) is False,
          "apply returns False when nothing is configured")
    reddit = _platform(**REDDIT_KWARGS)
    twitter = _platform(**TWITTER_KWARGS)
    check(reddit.refresh_rec_post_count == 5, "reddit capacity stays 5")
    check(twitter.refresh_rec_post_count == 2, "twitter capacity stays 2")
    check(cap.capacity_stats()["changes"] == [], "no changes recorded")


def case_legacy_slots():
    """reddit_config.feed_slots=2 hits the forum instance and nothing else."""
    import polylop_archetypes as arch
    import polylop_feed_capacity as cap
    config = {"reddit_config": {"feed_slots": 2}, "twitter_config": {}}
    arch.apply_archetypes(config)
    cap.apply_feed_capacity(config)
    reddit = _platform(**REDDIT_KWARGS)
    twitter = _platform(**TWITTER_KWARGS)
    check(reddit.refresh_rec_post_count == 2, "forum capacity 5 -> 2")
    check(twitter.refresh_rec_post_count == 2,
          "micro_broadcast untouched (still its default 2)")
    changes = cap.capacity_stats()["changes"]
    check(changes == [{"archetype": "forum", "from": 5, "to": 2}],
          "exactly one recorded change, keyed by archetype")


def case_two_instances_same_archetype():
    """The knob hits every instance of the archetype - the old recsys-keyed
    module behaved the same way; this pins it down for the N-platform
    future."""
    import polylop_archetypes as arch
    import polylop_feed_capacity as cap
    config = {"reddit_config": {"feed_slots": 3}}
    arch.apply_archetypes(config)
    cap.apply_feed_capacity(config)
    first = _platform(**REDDIT_KWARGS)
    second = _platform(**REDDIT_KWARGS)
    check(first.refresh_rec_post_count == 3, "first forum instance -> 3")
    check(second.refresh_rec_post_count == 3, "second forum instance -> 3")


def case_env_override():
    """POLYLOP_FEED_SLOTS overrides every archetype (unchanged behaviour)."""
    import polylop_archetypes as arch
    import polylop_feed_capacity as cap
    arch.apply_archetypes({})
    cap.apply_feed_capacity({})
    reddit = _platform(**REDDIT_KWARGS)
    twitter = _platform(**TWITTER_KWARGS)
    check(reddit.refresh_rec_post_count == 3, "override hits forum (5 -> 3)")
    check(twitter.refresh_rec_post_count == 3,
          "override hits micro_broadcast (2 -> 3)")


def case_off_switch():
    """POLYLOP_FEED_CAPACITY=off wins over configured slots (unchanged)."""
    import polylop_archetypes as arch
    import polylop_feed_capacity as cap
    config = {"reddit_config": {"feed_slots": 2}}
    arch.apply_archetypes(config)
    check(cap.apply_feed_capacity(config) is False, "apply returns False")
    reddit = _platform(**REDDIT_KWARGS)
    check(reddit.refresh_rec_post_count == 5, "capacity untouched")


def case_replay():
    """A consumer registered after platform construction still gets it."""
    import polylop_archetypes as arch
    import polylop_feed_capacity as cap
    config = {"reddit_config": {"feed_slots": 2}}
    arch.apply_archetypes(config)
    reddit = _platform(**REDDIT_KWARGS)
    check(reddit.refresh_rec_post_count == 5, "before consumer: unchanged")
    cap.apply_feed_capacity(config)
    check(reddit.refresh_rec_post_count == 2,
          "replay applies the knob to the pre-existing instance")


def case_idempotent():
    """Applying everything twice must not double anything."""
    import polylop_archetypes as arch
    import polylop_feed_capacity as cap
    config = {"reddit_config": {"feed_slots": 2}}
    arch.apply_archetypes(config)
    arch.apply_archetypes(config)
    cap.apply_feed_capacity(config)
    cap.apply_feed_capacity(config)
    reddit = _platform(**REDDIT_KWARGS)
    check(reddit.refresh_rec_post_count == 2, "capacity applied once")
    check(len(arch.archetype_stats()["instances"]) == 1,
          "instance registered exactly once")
    check(len(cap.capacity_stats()["changes"]) == 1,
          "exactly one change recorded")


def case_posting_scope():
    """Per-channel poster sets; calls without channel keep the old global
    behaviour (backward compatibility)."""
    import polylop_posting_rate as pr
    from oasis.social_agent.agent_action import SocialAction
    from oasis.social_agent.agent_environment import SocialEnvironment

    async def fake_prompt(self, *args, **kwargs):
        return "BASE"

    SocialEnvironment.to_text_prompt = fake_prompt

    config = {"reddit_config": {"posting_rate": True},
              "agent_configs": [{"agent_id": 1, "posts_per_hour": 1000.0},
                                {"agent_id": 2, "posts_per_hour": 1000.0}]}
    check(pr.apply_posting_rate(config) is True, "posting rate enabled")

    channel_a, channel_b = object(), object()
    chosen_a = pr.select_posters([1], 60, channel=channel_a)
    chosen_b = pr.select_posters([2], 60, channel=channel_b)
    check(chosen_a == {1} and chosen_b == {2},
          "rate 1000/h draws every active agent")

    def prompt_for(agent_id, channel):
        env = SocialEnvironment(SocialAction(agent_id, channel))
        return asyncio.run(env.to_text_prompt())

    check(prompt_for(1, channel_a).endswith(pr.NUDGE),
          "agent 1 nudged on its own platform")
    check(prompt_for(1, channel_b) == "BASE",
          "agent 1 NOT nudged on the other platform (pre-011 race is gone)")
    check(prompt_for(2, channel_b).endswith(pr.NUDGE),
          "agent 2 nudged on platform B")

    legacy_channel = object()
    pr.select_posters([2], 60)  # no channel -> global scope
    check(prompt_for(2, legacy_channel).endswith(pr.NUDGE),
          "channel-less draw still reaches agents (legacy global fallback)")
    check(prompt_for(1, legacy_channel) == "BASE",
          "global scope only nudges the drawn agent")


def case_influence_compat():
    """Influence patches and the archetype layer coexist on one Platform."""
    import polylop_archetypes as arch
    import polylop_influence as infl
    config = {"agent_configs": [{"agent_id": 0, "influence_weight": 3.0},
                                {"agent_id": 1, "influence_weight": 0.5}]}
    check(infl.apply_influence_patches(config) is True, "influence applied")
    arch.apply_archetypes(config)
    reddit = _platform(**REDDIT_KWARGS)
    check(arch.archetype_of(reddit) == "forum",
          "archetype registered on the influence-patched Platform")
    check(reddit.refresh.__name__ == "polylop_refresh",
          "influence refresh wrapper still in place")
    check(infl.weight_of(0) == 3.0, "influence weights loaded")


def case_resolve_entries():
    """PATCH-012: platform list resolution — legacy fallback and validation."""
    import polylop_archetypes as arch
    legacy = arch.resolve_platform_entries({})
    check(legacy == [{"name": "twitter", "archetype": "micro_broadcast"},
                     {"name": "reddit", "archetype": "forum"}],
          "no platforms key -> exactly the inherited pair")
    entries = arch.resolve_platform_entries(
        {"platforms": [{"name": "community", "archetype": "forum",
                        "feed_slots": 2}]})
    check(entries[0]["feed_slots"] == 2, "entry knobs survive validation")
    for bad in ({"platforms": [{"name": "x", "archetype": "nope"}]},
                {"platforms": [{"archetype": "forum"}]},
                {"platforms": [{"name": "a", "archetype": "forum"},
                               {"name": "a", "archetype": "forum"}]}):
        try:
            arch.resolve_platform_entries(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid platforms accepted: {bad!r}")
    print("  ok - unknown archetype / missing name / duplicate name raise")


def case_build_params_pin():
    """PATCH-012: build_platform mirrors DefaultPlatformType exactly."""
    import oasis
    import polylop_archetypes as arch
    from oasis.social_agent.agent_graph import AgentGraph
    arch.apply_archetypes({})

    pinned_attrs = ("recsys_type", "refresh_rec_post_count", "max_rec_post_len",
                    "following_post_count", "show_score", "allow_self_rating")

    for archetype, default_type in (
            ("forum", oasis.DefaultPlatformType.REDDIT),
            ("micro_broadcast", oasis.DefaultPlatformType.TWITTER)):
        reference_env = oasis.make(agent_graph=AgentGraph(),
                                   platform=default_type,
                                   database_path=":memory:")
        built = arch.build_platform(archetype, ":memory:")
        for attr in pinned_attrs:
            ref_val = getattr(reference_env.platform, attr)
            got_val = getattr(built, attr)
            check(got_val == ref_val,
                  f"{archetype}.{attr} == DefaultPlatformType value ({ref_val})")


def case_explicit_registration():
    """PATCH-012: build_platform registers explicitly, knobs from the entry."""
    import polylop_archetypes as arch
    import polylop_feed_capacity as cap
    config = {"platforms": [{"name": "community", "archetype": "forum",
                             "feed_slots": 3}]}
    arch.apply_archetypes(config)
    cap.apply_feed_capacity(config)
    entry = config["platforms"][0]
    platform = arch.build_platform("forum", ":memory:",
                                   knobs=arch.entry_knobs(entry, config),
                                   label="community")
    check(arch.archetype_of(platform) == "forum", "explicit archetype set")
    check(platform.refresh_rec_post_count == 3,
          "entry feed_slots applied without any legacy config section")
    merged = arch.entry_knobs({"name": "reddit", "archetype": "forum"},
                              {"reddit_config": {"feed_slots": 2, "posting_rate": True}})
    check(merged["feed_slots"] == 2 and merged["posting_rate"] is True,
          "legacy section knobs survive the merge for legacy entries")
    overridden = arch.entry_knobs(
        {"name": "reddit", "archetype": "forum", "feed_slots": 4},
        {"reddit_config": {"feed_slots": 2}})
    check(overridden["feed_slots"] == 4, "entry overrides legacy section")


def case_business_builder():
    """PATCH-013: the business-network builder produces agents with the
    archetype's own system prompt and action subset; classification of the
    inherited archetypes is untouched."""
    import json
    import tempfile
    import polylop_archetypes as arch

    arch.apply_archetypes({})

    # a twhin platform still classifies as micro_broadcast, never business
    twitter = _platform(**TWITTER_KWARGS)
    check(arch.archetype_of(twitter) == "micro_broadcast",
          "twhin recsys still classifies as micro_broadcast")

    profiles = [{
        "username": "anna_m_82", "name": "Anna Muster", "bio": "PR lead",
        "persona": "Anna is a seasoned PR strategist who cares about brand "
                   "reputation.",
        "profession": "Head of Communications", "gender": "female",
        "age": 41, "mbti": "ENTJ", "country": "Germany",
    }]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(profiles, fh)
        path = fh.name

    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    dummy_model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
        model_type="dummy-model", url="http://localhost:9", api_key="dummy")

    graph = asyncio.run(arch.build_agent_graph("business_network", path,
                                               dummy_model))
    agents = list(graph.get_agents())
    check(len(agents) == 1, "one agent built from one profile")
    agent = agents[0][1]

    system = agent.system_message.content
    check("professional business network" in system,
          "system prompt carries the business-network framing")
    check("Anna Muster" in system and "Head of Communications" in system,
          "real name and profession are in the system prompt")
    check("seasoned PR strategist" in system,
          "persona text survives into the system prompt")
    check("You're a Reddit user" not in system,
          "stock Reddit framing is gone")

    tool_names = {t.func.__name__ for t in agent.action_tools}
    check("dislike_post" not in tool_names and "dislike_comment" not in tool_names,
          "no public downvote on the business network")
    check({"repost", "quote_post", "create_comment"} <= tool_names,
          "share and comment mechanics available")

    os.unlink(path)


CASES = {
    "classify": (case_classify, {}),
    "noop": (case_noop, {}),
    "legacy_slots": (case_legacy_slots, {}),
    "two_instances": (case_two_instances_same_archetype, {}),
    "env_override": (case_env_override, {"POLYLOP_FEED_SLOTS": "3"}),
    "off_switch": (case_off_switch, {"POLYLOP_FEED_CAPACITY": "off"}),
    "replay": (case_replay, {}),
    "idempotent": (case_idempotent, {}),
    "posting_scope": (case_posting_scope, {}),
    "influence_compat": (case_influence_compat, {}),
    "resolve_entries": (case_resolve_entries, {}),
    "build_params_pin": (case_build_params_pin, {}),
    "explicit_registration": (case_explicit_registration, {}),
    "business_builder": (case_business_builder, {}),
}

_POLYLOP_VARS = ("POLYLOP_FEED_CAPACITY", "POLYLOP_FEED_SLOTS",
                 "POLYLOP_POSTING_RATE", "POLYLOP_INFLUENCE",
                 "POLYLOP_INFLUENCE_BOOST")


def main():
    if len(sys.argv) == 2:  # child mode: run one case in this process
        name = sys.argv[1]
        func, _ = CASES[name]
        print(f"case {name}:")
        func()
        return

    failed = []
    for name, (_, extra_env) in CASES.items():
        env = {k: v for k, v in os.environ.items()
               if k not in _POLYLOP_VARS}
        env.update(extra_env)
        result = subprocess.run([sys.executable, __file__, name], env=env)
        if result.returncode != 0:
            failed.append(name)
    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)
    print(f"all {len(CASES)} cases passed")


if __name__ == "__main__":
    main()
