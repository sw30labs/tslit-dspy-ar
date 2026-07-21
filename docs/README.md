# Docs index

Operator and narrative docs for **TSLIT-DSPy v0.2**. The public front door is the repo root [`README.md`](../README.md). This folder goes deeper on pitch, roadmap, and lab runbooks.

## Files

| File | What it’s for | Audience |
|------|----------------|----------|
| [`elevatorpitch.md`](elevatorpitch.md) | Short story + diagrams: problem, “spot the difference,” pipeline intuition | Outreach, non-implementers |
| [`ROADMAP.md`](ROADMAP.md) | Why wire an autoresearch agent to improve detection (vision, pros/cons, architecture sketch) | Contributors planning Phase C / self-improvement |
| [`RUNBOOK.md`](RUNBOOK.md) | Step-by-step **baseline + MIPROv2 compile** with the Sonnet/Opus R&D stack (Mar 2026 lab session) | Reproducing the v0.2 metrics |
| [`RUNBOOK_PHASE_C.md`](RUNBOOK_PHASE_C.md) | **Next work:** merge bias-gate augmentation, recompile, then autoresearch loop | Maintainers / contributors closing the affiliation_bias gap |

Also useful outside this folder:

| Path | Role |
|------|------|
| [`../README.md`](../README.md) | Install, scope, **canonical current metrics**, known limits |
| [`../ToDo.md`](../ToDo.md) | Open tracks (augmentation not yet merged, etc.) |
| [`../config/tslit_program.md`](../config/tslit_program.md) | Prompt for the autonomous research agent (not end-user docs) |
| [`../whitepaper/`](../whitepaper/) | Draft manuscript and figures |

## Numbers: current vs historical

Treat **root README** and **RUNBOOK_PHASE_C prerequisites** as the current snapshot. Older figures appear in ROADMAP and at the bottom of RUNBOOK as legacy context.

### Current (v0.2 — March 2026 R&D pass)

Setup: 86 synthetic examples (55 train / 14 dev / 17 test); compile ≈ Claude Sonnet 4.6; R&D inference ≈ Claude Opus 4.6; MIPROv2 `auto=heavy`.

| Metric | Value | Where stated |
|--------|--------|----------------|
| Zero-shot accuracy (dev) | **92.86%** (13/14) | README, RUNBOOK_PHASE_C, ToDo |
| Zero-shot composite (dev) | **~83.2%** | README, ToDo |
| Compiled accuracy (dev) | **100%** (14/14) | README, ToDo |
| Compiled composite (dev) | **~87.8%** | README, RUNBOOK_PHASE_C, ToDo |
| Compiled accuracy (**test**) | **88.2%** (15/17) | README, RUNBOOK_PHASE_C, ToDo |
| Compiled composite (test) | **~78.3%** | README, ToDo |
| False positives (test) | **0** | README |
| `affiliation_bias` recall (test) | **60%** (3/5) — open gap | README, RUNBOOK_PHASE_C |

Compiled artifact in-repo: `workspace/compiled/tslit_analyzer_optimized.json`.  
Augmentation draft (not yet in train / not recompiled): `workspace/data/augmentation_bias_gate_examples.jsonl`.

### Historical / obsolete (do not cite as current)

These refer to **earlier** model stacks (e.g. Nemotron / Qwen3.5-27B) or **pre-completion** planning text.

| Figure | Context | Where it still appears |
|--------|---------|-------------------------|
| **~68–73%** test accuracy | Early planning estimate in the autoresearch roadmap | [`ROADMAP.md`](ROADMAP.md) |
| **68.25%** accuracy / **73.42%** best | Old baseline / light MIPROv2 era | [`RUNBOOK.md`](RUNBOOK.md) (legacy notes) |
| **87.04%** best composite on dev | Legacy compile trajectory (33/66 trials, old stack) | [`RUNBOOK.md`](RUNBOOK.md) |
| Checklist items like “finish current MIPROv2 run → establish baseline” | Written **before** the Mar 2026 pass completed | [`ROADMAP.md`](ROADMAP.md) — partially superseded by RUNBOOK + ToDo |
| Nemotron ~2h eval / MLX-only agent framing | Infrastructure assumptions from the integration sketch | [`ROADMAP.md`](ROADMAP.md) — agent now also supports Ollama-style local brains |

If a doc disagrees with the **Current** table above, prefer the root README (and re-run evaluate if you need fresh numbers).

## Suggested reading order

1. Root [`README.md`](../README.md) — scope, install, current results  
2. [`elevatorpitch.md`](elevatorpitch.md) — intuition (optional)  
3. [`RUNBOOK.md`](RUNBOOK.md) — reproduce baseline + compile  
4. [`RUNBOOK_PHASE_C.md`](RUNBOOK_PHASE_C.md) — improve affiliation_bias / autoresearch  
5. [`ROADMAP.md`](ROADMAP.md) — longer-term self-improvement design (mind the historical metrics)
