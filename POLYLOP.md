# Polylop

EU-focused fork of [nikmcfly/MiroFish-Offline](https://github.com/nikmcfly/MiroFish-Offline) (which itself forks [666ghj/MiroFish](https://github.com/666ghj/MiroFish), a multi-agent social simulation engine built on OASIS).

**Status:** concept phase, no product yet. Internal simulation engine for a planned EU-sovereign successor product. UI is branded "Polylop" throughout (rebranded 2026-06-13); the underlying repo/directory names are unchanged for continuity with upstream.

## Polylop-specific changes

- **Mistral Cloud as primary LLM** (Small/Large via OpenAI-compatible API) instead of local Ollama — local models deferred as currently uneconomical on available hardware
- **Host-Ollama for embeddings** (`nomic-embed-text` via `host.docker.internal:11434`); Ollama container removed from compose
- **24h timeout** on LLM calls, extended to all three OpenAI clients (main + persona generator + config generator; the latter two were initially missed, fixed 2026-07-15)
- **Two-tier LLM config**: `LLM_MODEL_NAME` for reasoning, `LLM_MODEL_NAME_FAST` for persona generation (custom Polylop tier; complements the upstream `GRAPH_LLM_*` tier from PR #41)
- **Automatic VRAM flush** after graph build (releases Ollama models from VRAM)
- **Persona generator patched**: DACH/EU defaults instead of hardcoded US/ISTJ bias, plus soft behavioral anchors (posting style, active hours) baked into the persona prompts
- **Backported upstream PRs**:
  - [#45](https://github.com/nikmcfly/MiroFish-Offline/pull/45) — macOS ARM wheels
  - [#30](https://github.com/nikmcfly/MiroFish-Offline/pull/30) — session persistence + dashboard
  - [#41](https://github.com/nikmcfly/MiroFish-Offline/pull/41) — cloud-LLM tier + error recovery
  - [#51](https://github.com/nikmcfly/MiroFish-Offline/pull/51) — surface failed/empty builds, rebuild button, hardened polling (own fix on top: health-check false-negative on HTTP error responses)
  - [#50](https://github.com/nikmcfly/MiroFish-Offline/pull/50) — LLM retry/backoff on rate limits, Neo4j connection guards, 0-entities-is-a-failure (embedding portion of this PR intentionally skipped — our own #41-based embedding service already covers it better)
- **Fixed in this fork**: removed hardcoded `wealthiq.ngrok.app` from Vite `allowedHosts` (accidentally introduced by upstream PR #30); corrected misleading "100% offline / no cloud" copy in the UI to reflect the actual Mistral-EU-cloud setup

## Not backported

- [#48](https://github.com/nikmcfly/MiroFish-Offline/pull/48) — Ollama GPU tuning; not applicable, this fork doesn't run Ollama in its own compose
- [#52](https://github.com/nikmcfly/MiroFish-Offline/pull/52) — sync from the actively-developed 666ghj/MiroFish; too broad, unclear benefit, high divergence risk

## Upstream status

The direct upstream [nikmcfly/MiroFish-Offline](https://github.com/nikmcfly/MiroFish-Offline) has not seen commits since March 2026 — this fork is the de-facto maintained branch. The original [666ghj/MiroFish](https://github.com/666ghj/MiroFish) remains active but on a different stack (Zep Cloud + cloud LLMs) and is not synced from here.

## License

AGPL-3.0, inherited from upstream. Any modification or service use requires source disclosure.

See [`README.md`](./README.md) for upstream setup and architecture.
