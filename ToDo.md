# ToDo — TSLIT-DSPy v0.2

Research release: **code now, paper soon.** Honest open items for contributors and maintainers.

## Completed
- [x] Zero-shot baseline with Opus 4.6 on dev (2026-03-25): **92.86% accuracy, 83.2% composite** (13/14)
- [x] MIPROv2 compile Sonnet 4.6 + Opus 4.6 inference (heavy, 66 trials): **87.29% best composite**
- [x] Dev eval of compiled model (2026-03-26): **100% accuracy (14/14), 87.8% composite**
- [x] Held-out test eval (2026-03-26): **88.2% accuracy (15/17), 78.3% composite** — 2 affiliation_bias FNs, zero FPs
- [x] Whitepaper Section 6 updated (incl. held-out test subsection)
- [x] Draft 10 augmentation examples (`workspace/data/augmentation_bias_gate_examples.jsonl`)
- [x] `agent_loop_mlx.py` Ollama health-check support
- [x] Phase C runbook (`docs/RUNBOOK_PHASE_C.md`)
- [x] Public hygiene for v0.2: LICENSE (Apache-2.0), SECURITY.md, README research framing, remove personal console dump / LaTeX build junk

## Next — Track 1 (manual augmentation + recompile)
- [ ] Append augmentation examples to train.jsonl (55 → 65 examples)
- [ ] Recompile with augmented training set — see `docs/RUNBOOK_PHASE_C.md`
- [ ] Evaluate on dev set
- [ ] Evaluate on test set (target: affiliation_bias recall > 0.80)

## Next — Track 2 (Phase C autoresearch, after Track 1)
- [ ] Launch autoresearch agent with local open model brain — see `docs/RUNBOOK_PHASE_C.md`
- [ ] Monitor agent-generated examples; validate with `--mini` runs
- [ ] Full recompile after 3–5 validated examples
- [ ] Document compute budget (API hours / GPU-days) actually required per outer-loop gain

## Later
- [ ] Deployment validation pass on fully open local stack (e.g. GPT-OSS via Ollama)
- [ ] Regenerate Figure 6 (MIPROv2 trajectory) with current data
- [ ] Update `docs/RUNBOOK.md` with measured compile/eval timings
- [ ] Automated tests + CI smoke (schema, metrics, JSONL load)
- [ ] Whitepaper polish → arXiv / venue submission
- [ ] Optional AISC follow-up once paper narrative is stable
