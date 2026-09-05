# TSLIT-DSPy Command Deck

A single-page command deck for the TSLIT-DSPy-AR workbench, styled after the
Contingency Atlas / Book Buddy dashboards. It surfaces **all** project
functionality and documentation in one local UI and drives inference through
the same **OMLX** backend the sibling projects use.

```
python -m tslit_dspy.web            # http://127.0.0.1:8780
tslit-serve                         # (if installed via pip)
```

## Views

| View | What it does |
|------|--------------|
| **Overview** | Situation room — dataset counts, pipeline stages, threat categories, live event log. |
| **Analyze** | Probe a single model response through the TSLITAnalyzer (zero-shot or compiled) over OMLX. |
| **Evaluate** | Run a full evaluation against `test.jsonl` / `dev.jsonl` — accuracy, composite, per-class precision/recall/F1, per-example table. |
| **Data** | Dataset register (train/dev/test/augmentation), compiled models, persisted eval reports, and an augmentation appender to `train.jsonl`. |
| **Compiled** | Inspect the MIPROv2-compiled analyzer prompts and demos (the "no black box" guarantee). |
| **GPU Channel** | `tslit_hw` GPU-observability docs + prototype runner overview. |
| **Autoresearch** | Phase C research program, agent loop source, experiment runner, and runbook. |
| **Docs** | Every markdown/config/source file in the repo, readable in the deck. |
| **About** | Project overview, stack, author, CLI. |

## Backend

Same LLM convention as Book Buddy / Contingency Atlas:

- **omlx** (default) — local OMLX at `http://127.0.0.1:8000/v1`, key from
  `OMLX_API_KEY` (default `test`). Model e.g. `DeepSeek-V4-Flash-0731-MLX`.
- **vllm** (alias `dgx`) — DGX/vLLM, requires a `base_url`.

## API

The deck is a stdlib-only `ThreadingHTTPServer` (no web dependencies). Endpoints:

- `GET /api/health` — server + busy state + project meta
- `GET /api/backend?backend=omlx&base_url=…` — OMLX reachability probe
- `GET /api/models` — models served by OMLX
- `GET /api/data` — dataset / compiled / eval-report inventory
- `GET /api/docs` — documentation tree
- `GET /api/docs/content?path=…` — file contents (path-traversal safe)
- `GET /api/compiled/inspect` — compiled-model prompt inspection
- `GET /api/state`, `/api/events`, `/api/jobs` — run state, live events, job history
- `POST /api/run` — start an `evaluate` / `analyze` / `probe` job (one at a time)
- `POST /api/append` — append JSONL examples to `train.jsonl` (augmentation)

## Job model

One job runs at a time (single-writer guard, same as Book Buddy). The heavy
DSPy stack is imported lazily inside the worker thread, so the server always
boots and serves the SPA + docs even when `dspy` is not installed. Inference
uses `dspy.context(lm=…)` so the thread-affine DSPy settings are respected.

## Tests

```
.venv/bin/python -m pytest tests/test_web_deck.py
```

Tests boot the deck on an ephemeral port with the job runner stubbed (no
live OMLX required) and cover the SPA, data, docs, traversal safety,
compiled inspection, and run/append APIs.