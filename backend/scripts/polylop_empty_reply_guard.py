"""
Polylop guard against Mistral error 3240 ("Assistant message must have either content
or tool_calls, but not none").

The problem, measured on 2026-07-26 with a request recorder around
BaseModelBackend.arun:

    [2] assistant  tool_calls=yes  content=''      <- the tool call, valid
    [3] tool                       "{'success': True, ...}"
    [4] assistant  tool_calls=no   content=''      <- rejected by Mistral
    [5] user       "Please perform social media actions ..."

After a tool result CAMEL asks the model once more. Mistral frequently answers that
follow-up with nothing at all — no text, no tool call. CAMEL records that empty answer
in the agent's memory, where it stays for the rest of the run, so every later request
from that agent is rejected with a 400. That is why agents in a real run perform one to
three actions and are then dead for good: measured over a 12-agent run, 12 transitions
from success to failure, 137 failure-to-failure, and not a single recovery.

OpenAI accepts an assistant message with empty content, which is why the upstream code
never had to care.

Two guards, because the memory of an already-running simulation may be poisoned:

  1. record: an assistant message with neither text nor tool calls is not written to
     memory in the first place.
  2. send:   any such message still present is dropped from the outgoing request.

Neither changes agent behaviour — the action itself was already executed and recorded
via the tool call; only the model's empty closing remark is discarded.

Off switch: POLYLOP_EMPTY_GUARD=off
"""
import logging
import os

logger = logging.getLogger("polylop.empty_reply_guard")

_stats = {"dropped_on_record": 0, "dropped_on_send": 0}


def _is_empty_assistant(msg) -> bool:
    """True for an assistant message carrying neither content nor tool calls."""
    if not isinstance(msg, dict):
        return False
    if msg.get("role") != "assistant":
        return False
    if msg.get("tool_calls"):
        return False
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return False
    if content not in (None, "") and not isinstance(content, str):
        # structured content (e.g. a list of parts) — leave it alone
        return False
    return True


def _patch_record(ChatAgent) -> bool:
    """Do not let an empty model reply enter the agent's memory."""
    if getattr(ChatAgent, "_polylop_empty_guard_record", False):
        return False

    original = ChatAgent.record_message

    def record_message(self, message):
        content = getattr(message, "content", None)
        has_text = isinstance(content, str) and content.strip()
        meta = getattr(message, "meta_dict", None) or {}
        has_calls = bool(meta.get("tool_calls")) or getattr(message, "func_name", None)
        if not has_text and not has_calls:
            _stats["dropped_on_record"] += 1
            logger.debug(
                "Dropped an empty assistant reply before it reached memory "
                "(total: %d)", _stats["dropped_on_record"],
            )
            return None
        return original(self, message)

    ChatAgent.record_message = record_message
    ChatAgent._polylop_empty_guard_record = True
    return True


def _patch_send(BaseModelBackend) -> bool:
    """Drop empty assistant messages from requests, including from memory written
    before this guard was in place."""
    if getattr(BaseModelBackend, "_polylop_empty_guard_send", False):
        return False

    original_run = BaseModelBackend.run
    original_arun = BaseModelBackend.arun

    def _clean(messages):
        if not messages:
            return messages
        kept = [m for m in messages if not _is_empty_assistant(m)]
        removed = len(messages) - len(kept)
        if removed:
            _stats["dropped_on_send"] += removed
            logger.debug(
                "Removed %d empty assistant message(s) from an outgoing request "
                "(total: %d)", removed, _stats["dropped_on_send"],
            )
        return kept

    def run(self, messages, *args, **kwargs):
        return original_run(self, _clean(messages), *args, **kwargs)

    async def arun(self, messages, *args, **kwargs):
        return await original_arun(self, _clean(messages), *args, **kwargs)

    BaseModelBackend.run = run
    BaseModelBackend.arun = arun
    BaseModelBackend._polylop_empty_guard_send = True
    return True


def apply_empty_reply_guard() -> bool:
    """Install both guards. Safe to call more than once."""
    if os.environ.get("POLYLOP_EMPTY_GUARD", "").lower() in ("off", "0", "false"):
        print("POLYLOP-EMPTY-GUARD disabled via POLYLOP_EMPTY_GUARD")
        return False

    from camel.agents import ChatAgent
    from camel.models.base_model import BaseModelBackend

    recorded = _patch_record(ChatAgent)
    sent = _patch_send(BaseModelBackend)

    print(
        "POLYLOP-EMPTY-GUARD-ACTIVE "
        f"record_patch={'installed' if recorded else 'already-present'} "
        f"send_patch={'installed' if sent else 'already-present'} "
        "(guards against Mistral error 3240)"
    )
    return True


def guard_stats() -> dict:
    """How often each guard fired — useful to see how often the model answers empty."""
    return dict(_stats)
