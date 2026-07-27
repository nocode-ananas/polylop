"""
Polylop: platform archetypes — declarative definitions plus an instance
registry.

CUSTOM (Polylop): no upstream counterpart in MiroFish-Offline. First step of
the archetype library (POL-ARCH01, PATCH-011); platform construction for the
generic runner added in PATCH-012.

Why
---
The simulation has known exactly two platforms so far, hard-wired as
``DefaultPlatformType.REDDIT`` / ``.TWITTER``, and the Polylop patch modules
told them apart by ``recsys_type``. That key stops working the moment two
archetypes share a base recsys (a business-network archetype runs on the same
personalized recsys as micro-broadcast). This module gives every ``Platform``
*instance* an archetype identity instead:

- ``ARCHETYPES``: declarative definitions. The two inherited ones — ``forum``
  (today's Reddit setup) and ``micro_broadcast`` (today's Twitter setup) —
  mirror OASIS' DefaultPlatformType values exactly. New archetypes arrive
  only together with the code that consumes them; this project keeps finding
  config that nothing reads (influence_weight, posts_per_hour, the
  PlatformConfig weights), so no field is added here before something
  consumes it.
- a registry: ``Platform`` instance -> (archetype name, knobs). Instances are
  classified when they are constructed, or registered explicitly when built
  through ``build_platform``. Consumers (feed capacity, posting rate, future
  prompt/action layers) look their parameters up here instead of guessing
  from ``recsys_type``.
- ``on_platform(callback)``: consumers register a hook that runs once per new
  ``Platform`` instance with (platform, archetype_name, knobs). Hooks are
  replayed for instances that already exist, so registration order does not
  matter.
- ``resolve_platform_entries`` / ``build_platform`` (PATCH-012): the generic
  runner reads its platform list from ``config["platforms"]``; configs
  without that key resolve to exactly the inherited twitter+reddit pair.

Entry shape in ``config["platforms"]``:

    {"name": "reddit", "archetype": "forum"}

plus optional knobs consumed by the patch modules (``feed_slots``,
``posting_rate``), an optional ``llm`` ("common"/"boost", defaults per
archetype) and an optional ``profiles`` file name (defaults per archetype).
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("polylop.archetypes")

MARKER = "POLYLOP-ARCHETYPES"

# ``platform_params`` mirror oasis/environment/env.py (camel-oasis 0.2.5,
# DefaultPlatformType branch) exactly — pinned by a test against a
# DefaultPlatformType-built instance. ``actions`` are ActionType values,
# resolved lazily so importing this module never needs oasis.
ARCHETYPES: Dict[str, Dict[str, Any]] = {
    "forum": {
        "base_recsys": "reddit",
        "legacy_config_key": "reddit_config",
        "platform_params": {
            "recsys_type": "reddit",
            "allow_self_rating": True,
            "show_score": True,
            "max_rec_post_len": 100,
            "refresh_rec_post_count": 5,
        },
        "profile_format": "reddit_json",
        "default_profiles": "reddit_profiles.json",
        "default_llm": "boost",
        "actions": [
            "like_post", "dislike_post", "create_post", "create_comment",
            "like_comment", "dislike_comment", "search_posts", "search_user",
            "trend", "refresh", "do_nothing", "follow", "mute",
        ],
    },
    "micro_broadcast": {
        "base_recsys": "twhin-bert",
        "legacy_config_key": "twitter_config",
        "platform_params": {
            "recsys_type": "twhin-bert",
            "refresh_rec_post_count": 2,
            "max_rec_post_len": 2,
            "following_post_count": 3,
        },
        "profile_format": "twitter_csv",
        "default_profiles": "twitter_profiles.csv",
        "default_llm": "common",
        "actions": [
            "create_post", "like_post", "repost", "follow", "do_nothing",
            "quote_post",
        ],
    },
}

# PATCH-013: first archetype beyond the two inherited ones. The behaviour
# lever is threefold: an action subset without public downvotes, a system
# prompt that puts the agent under real-name professional visibility, and a
# mildly personalized recsys. Whether the prompt lever actually shifts
# behaviour is a measured claim (see POL-ARCH01 / changelog), not an assumed
# one.
BUSINESS_NETWORK_TEMPLATE = """
# OBJECTIVE
You're a member of a professional business network (comparable to LinkedIn), and I'll present you with some posts. After you see the posts, choose some actions from the following functions.

# PLATFORM RULES
You appear under your real name with your professional role visible to everyone. Your employer, your colleagues, your clients and potential business partners read what you write here. Posts and comments are public, permanent and quotable. Unprofessional behaviour has real career costs. People on this platform therefore write in a professional register: polite, constructive, image-conscious. They avoid insults, rants, slang and oversharing, they highlight their expertise and experience, and they consider how a statement reflects on themselves and their company before publishing it. Disagreement is voiced diplomatically and backed with arguments.

# SELF-DESCRIPTION
Your name is {name}. You work as {profession}. Your actions should be consistent with your self-description and personality, expressed the way this professional environment demands.
{persona}
You are a {gender}, {age} years old, with an MBTI personality type of {mbti} from {country}.

# RESPONSE METHOD
Please perform actions by tool calling.
"""

ARCHETYPES["business_network"] = {
    # never classified implicitly - reachable only through an explicit
    # platforms entry
    "base_recsys": None,
    "legacy_config_key": None,
    # Platform params identical to forum on purpose: the personalized recsys
    # paths (twhin-bert / SentenceTransformer) download HF models at runtime
    # into the ephemeral container FS - not production-ready here. Until an
    # embedding model is baked into the image, business_network runs on the
    # proven hot-score recsys; the A/B measurement against forum then
    # isolates exactly the prompt+action lever.
    "platform_params": {
        "recsys_type": "reddit",
        "allow_self_rating": True,
        "show_score": True,
        "max_rec_post_len": 100,
        "refresh_rec_post_count": 5,
    },
    "profile_format": "reddit_json",
    "default_profiles": "reddit_profiles.json",
    "default_llm": "common",
    # no dislike_*: there is no public downvote on a business network.
    # repost/quote are the share mechanics; mute exists but no block drama.
    "actions": [
        "create_post", "create_comment", "like_post", "like_comment",
        "repost", "quote_post", "follow", "do_nothing", "search_posts",
        "search_user", "refresh", "mute",
    ],
    "system_template": BUSINESS_NETWORK_TEMPLATE,
}

# PATCH-014: newsletter — the first archetype with role asymmetry. One or a
# few sender agents publish issues; every other agent is a reader who cannot
# post at all (enforced by the per-agent tool subset, not by prompt). Replies
# are modeled as comments but framed as private mail to the author, and
# readers do not see other readers' replies in their environment
# (hide_comments_in_feed) — the closest OASIS gets to a 1->N channel without
# a real private-message mechanic. No algorithm: every issue reaches every
# reader (random recsys with capacity far above any realistic issue count).
NEWSLETTER_SENDER_TEMPLATE = """
# OBJECTIVE
You write and publish an email newsletter that goes directly to the inboxes of your subscribers. I'll show you your platform environment. Choose actions from the following functions.

# PLATFORM RULES
You are the author of this newsletter. Everything you post is an issue that lands unfiltered in every subscriber's inbox under your name - there is no algorithm between you and your readers. Subscribers may reply to you; a reply is a private message to you, not a public discussion. Your readers subscribed because they value your voice: write in it - personal, considered, worth their attention. Every issue costs attention, so never send filler.

# SELF-DESCRIPTION
Your name is {name}. You work as {profession}.
{persona}
You are a {gender}, {age} years old, with an MBTI personality type of {mbti} from {country}.

# RESPONSE METHOD
Please perform actions by tool calling.
"""

NEWSLETTER_READER_TEMPLATE = """
# OBJECTIVE
You are a subscriber of an email newsletter. New issues appear in your private inbox. I'll show you what arrived. Choose actions from the following functions.

# PLATFORM RULES
This is your private inbox, not a public feed. The issues you see were written by the author you subscribed to. You cannot publish anything here yourself. You can read, you can like an issue (that quietly tells the author it landed well), and you can write a reply - a private message that only the author will read, like answering an email. People reply in a personal, direct tone, writing to one person rather than performing for an audience.

# SELF-DESCRIPTION
Your name is {name}. You work as {profession}.
{persona}
You are a {gender}, {age} years old, with an MBTI personality type of {mbti} from {country}.

# RESPONSE METHOD
Please perform actions by tool calling.
"""

ARCHETYPES["newsletter"] = {
    # explicit only, like business_network
    "base_recsys": None,
    "legacy_config_key": None,
    "platform_params": {
        "recsys_type": "random",
        # capacity far above any realistic issue count: every reader sees
        # every issue, nothing competes for feed slots
        "refresh_rec_post_count": 50,
        "max_rec_post_len": 100,
        "allow_self_rating": False,
        "show_score": False,
    },
    "profile_format": "reddit_json",
    "default_profiles": "reddit_profiles.json",
    "default_llm": "common",
    # top-level actions unused (roles below define per-agent sets); kept for
    # archetype_actions() callers as the union of both roles
    "actions": ["create_post", "create_comment", "like_post", "like_comment",
                "refresh", "do_nothing"],
    "roles": {
        "sender": {
            "actions": ["create_post", "create_comment", "like_comment",
                        "refresh", "do_nothing"],
            "system_template": NEWSLETTER_SENDER_TEMPLATE,
        },
        "reader": {
            "actions": ["like_post", "create_comment", "refresh",
                        "do_nothing"],
            "system_template": NEWSLETTER_READER_TEMPLATE,
            "hide_comments_in_feed": True,
        },
    },
    "default_role": "reader",
}

_state: Dict[str, Any] = {"applied": False, "config": {}, "pending": None}
# Strong refs are fine: a handful of Platform instances per process, and the
# process ends with the simulation. id() stays unique while the ref lives.
_instances: List[Tuple[Any, Optional[str], Dict[str, Any]]] = []
_by_id: Dict[int, Tuple[Any, Optional[str], Dict[str, Any]]] = {}
_callbacks: List[Callable[[Any, Optional[str], Dict[str, Any]], None]] = []


def _fire(callback, platform, name, knobs) -> None:
    # A consumer hook must never break a simulation.
    try:
        callback(platform, name, knobs)
    except Exception as exc:
        logger.error("%s consumer hook %r failed: %s", MARKER,
                     getattr(callback, "__module__", callback), exc)


def _register(platform) -> None:
    recsys = getattr(platform.recsys_type, "value", str(platform.recsys_type))

    pending = _state.get("pending")
    if pending is not None:
        name, knobs, label = pending
        origin = f"explicit{': ' + label if label else ''}"
    else:
        name = None
        for archetype_name, spec in ARCHETYPES.items():
            if spec["base_recsys"] == recsys:
                name = archetype_name
                break
        knobs = {}
        if name is not None:
            key = ARCHETYPES[name]["legacy_config_key"]
            knobs = dict((_state["config"] or {}).get(key) or {})
        else:
            logger.warning("%s unclassified platform (recsys=%s) - no knobs",
                           MARKER, recsys)
        origin = "classified"

    entry = (platform, name, knobs)
    _instances.append(entry)
    _by_id[id(platform)] = entry
    print(f"{MARKER} platform recsys={recsys} -> archetype="
          f"{name or 'unclassified'} ({origin})")
    for callback in _callbacks:
        _fire(callback, platform, name, knobs)


def apply_archetypes(config: Dict[str, Any]) -> bool:
    """Start classifying Platform instances. Idempotent, call before
    ``oasis.make``."""
    if _state["applied"]:
        return True
    _state["config"] = config or {}

    import oasis.social_platform.platform as platform_mod

    if not getattr(platform_mod.Platform, "_polylop_archetypes", False):
        original_init = platform_mod.Platform.__init__

        def patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            _register(self)

        platform_mod.Platform.__init__ = patched_init
        platform_mod.Platform._polylop_archetypes = True

    _state["applied"] = True
    print(f"{MARKER}-ACTIVE known={','.join(ARCHETYPES)}")
    return True


def on_platform(
        callback: Callable[[Any, Optional[str], Dict[str, Any]], None]) -> None:
    """Register a consumer hook; replayed for already-known instances."""
    _callbacks.append(callback)
    for platform, name, knobs in list(_instances):
        _fire(callback, platform, name, knobs)


def archetype_of(platform) -> Optional[str]:
    entry = _by_id.get(id(platform))
    return entry[1] if entry else None


def knobs_of(platform) -> Dict[str, Any]:
    entry = _by_id.get(id(platform))
    return dict(entry[2]) if entry else {}


def archetype_stats() -> Dict[str, Any]:
    return {
        "applied": _state["applied"],
        "instances": [
            {"archetype": name or "unclassified",
             "recsys": getattr(platform.recsys_type, "value",
                               str(platform.recsys_type))}
            for platform, name, _ in _instances
        ],
    }


# --------------------------------------------------------------------------
# PATCH-012: platform list resolution + explicit construction
# --------------------------------------------------------------------------

def resolve_platform_entries(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the validated platform entries for a run.

    ``config["platforms"]`` when present; otherwise exactly the inherited
    twitter+reddit pair (the pre-PATCH-012 behaviour of the parallel runner).
    Raises on unknown archetypes or duplicate names — a mistyped archetype
    must fail loudly, not silently simulate something else.
    """
    entries = (config or {}).get("platforms")
    if not entries:
        return [
            {"name": "twitter", "archetype": "micro_broadcast"},
            {"name": "reddit", "archetype": "forum"},
        ]
    validated = []
    seen = set()
    for entry in entries:
        entry = dict(entry or {})
        name = entry.get("name")
        archetype = entry.get("archetype")
        if not name or name in seen:
            raise ValueError(
                f"{MARKER} platforms entry needs a unique name: {entry!r}")
        if archetype not in ARCHETYPES:
            raise ValueError(
                f"{MARKER} unknown archetype {archetype!r} "
                f"(known: {', '.join(ARCHETYPES)})")
        seen.add(name)
        validated.append(entry)
    return validated


def entry_knobs(entry: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Effective knobs for one platform entry: the archetype's legacy config
    section as base (so old configs keep working), the entry itself on top."""
    spec = ARCHETYPES[entry["archetype"]]
    legacy_key = spec.get("legacy_config_key")
    knobs = dict((config or {}).get(legacy_key) or {}) if legacy_key else {}
    knobs.update(entry)
    return knobs


def build_platform(archetype_name: str, db_path: str,
                   knobs: Optional[Dict[str, Any]] = None,
                   label: Optional[str] = None):
    """Construct a Platform instance for an archetype and register it
    explicitly (wins over recsys classification)."""
    from oasis.social_platform.platform import Platform

    if not _state["applied"]:
        raise RuntimeError(f"{MARKER} build_platform needs apply_archetypes "
                           "first - the instance would go unregistered")
    spec = ARCHETYPES[archetype_name]
    _state["pending"] = (archetype_name, dict(knobs or {}), label)
    try:
        return Platform(db_path=db_path, **spec["platform_params"])
    finally:
        _state["pending"] = None


def archetype_actions(archetype_name: str):
    """The archetype's available actions as ActionType values (lazy oasis
    import)."""
    from oasis.social_platform.typing import ActionType
    return [ActionType(a) for a in ARCHETYPES[archetype_name]["actions"]]


def _hide_comments_in_feed(env) -> None:
    """Per-instance override of SocialEnvironment.get_posts_env that strips
    the comments from every post before it reaches the agent's prompt
    (PATCH-014: newsletter readers must not see other readers' replies).
    The platform DB and traces keep the full data."""
    import json as _json

    async def get_posts_env() -> str:
        posts = await env.action.refresh()
        if posts.get("success"):
            cleaned = []
            for post in posts["posts"]:
                post = dict(post)
                post.pop("comments", None)
                cleaned.append(post)
            return env.posts_env_template.substitute(
                posts=_json.dumps(cleaned, indent=4))
        return "After refreshing, there are no existing posts."

    env.get_posts_env = get_posts_env


async def build_agent_graph(archetype_name: str, profile_path: str, model,
                            knobs: Optional[Dict[str, Any]] = None):
    """Polylop graph builder for archetypes with their own system prompt
    (PATCH-013), and per-agent roles (PATCH-014).

    Mirrors oasis' generate_reddit_agent_graph, but hands every agent the
    archetype's system template via OASIS' native ``user_info_template``
    hook, with a flat profile dict matching the template placeholders.
    Reads the reddit-format profile JSON — the same persona set every other
    platform of the run uses, so identities stay consistent.

    Archetypes with a ``roles`` block get per-agent action subsets and
    templates: agent ids listed in the platform entry's ``senders`` knob
    become the sender role, everyone else the default role. The asymmetry
    is enforced by the tool subset, not by prompt text.
    """
    import json as _json

    from camel.prompts import TextPrompt
    from oasis.social_agent.agent import SocialAgent
    from oasis.social_agent.agent_graph import AgentGraph
    from oasis.social_platform.config import UserInfo
    from oasis.social_platform.typing import ActionType

    spec = ARCHETYPES[archetype_name]
    roles = spec.get("roles")
    senders = set()
    if roles:
        try:
            senders = {int(a) for a in (knobs or {}).get("senders", [])}
        except (TypeError, ValueError):
            senders = set()
        if not senders:
            raise ValueError(
                f"{MARKER} archetype {archetype_name!r} needs a non-empty "
                f"'senders' list (agent ids) in its platforms entry")

    with open(profile_path, "r", encoding="utf-8") as fh:
        agent_info = _json.load(fh)

    agent_graph = AgentGraph()
    for i, item in enumerate(agent_info):
        if roles:
            role_name = "sender" if i in senders else spec["default_role"]
            role = roles[role_name]
            template = TextPrompt(role["system_template"])
            actions = [ActionType(a) for a in role["actions"]]
        else:
            role = None
            template = TextPrompt(spec["system_template"])
            actions = archetype_actions(archetype_name)

        profile = {
            "name": item.get("name") or item.get("username") or f"Agent {i}",
            "profession": item.get("profession") or "a professional",
            "persona": item.get("persona") or "",
            "gender": item.get("gender") or "person",
            "age": item.get("age") or "adult",
            "mbti": item.get("mbti") or "unknown",
            "country": item.get("country") or "unknown",
        }
        user_info = UserInfo(
            name=item.get("username"),
            description=item.get("bio"),
            profile=profile,
            recsys_type=spec["platform_params"]["recsys_type"],
        )
        agent = SocialAgent(
            agent_id=i,
            user_info=user_info,
            user_info_template=template,
            agent_graph=agent_graph,
            model=model,
            available_actions=actions,
        )
        if role and role.get("hide_comments_in_feed"):
            _hide_comments_in_feed(agent.env)
        agent_graph.add_agent(agent)
    return agent_graph
