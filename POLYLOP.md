# Polylop

EU-focused fork of [nikmcfly/MiroFish-Offline](https://github.com/nikmcfly/MiroFish-Offline) (which itself forks [666ghj/MiroFish](https://github.com/666ghj/MiroFish), a multi-agent social simulation engine built on OASIS).

**Status:** concept phase, no product. Internal simulation engine for a planned EU-sovereign successor product.

## Polylop-specific changes

- **Mistral Cloud as primary LLM** (Small/Large via OpenAI-compatible API) instead of local Ollama
- **Host-Ollama for embeddings** (`nomic-embed-text` via `host.docker.internal:11434`); Ollama container removed from compose
- **24h timeout** on LLM calls (persona generation can be slow on Mistral)
- **Two-tier LLM config**: `LLM_MODEL_NAME` for reasoning, `LLM_MODEL_NAME_FAST` for persona generation (custom Polylop tier; complements the upstream `GRAPH_LLM_*` tier from PR #41)
- **Automatic VRAM flush** after graph build (releases Ollama models from VRAM)
- **Backported upstream PRs**: [#45](https://github.com/nikmcfly/MiroFish-Offline/pull/45) (macOS ARM wheels), [#30](https://github.com/nikmcfly/MiroFish-Offline/pull/30) (session persistence + dashboard), [#41](https://github.com/nikmcfly/MiroFish-Offline/pull/41) (Cloud-LLM tier + error recovery)
- **Fixed in this fork**: removed hardcoded `wealthiq.ngrok.app` from Vite `allowedHosts` (accidentally introduced by upstream PR #30)

## Upstream status

The direct upstream [nikmcfly/MiroFish-Offline](https://github.com/nikmcfly/MiroFish-Offline) has not seen commits since March 2026 — this fork is the de-facto maintained branch. The original [666ghj/MiroFish](https://github.com/666ghj/MiroFish) remains active but on a different stack (Zep Cloud + cloud LLMs) and is not synced from here.

## License

AGPL-3.0, inherited from upstream. Any modification or service use requires source disclosure.

See [`README.md`](./README.md) for upstream setup and architecture.
