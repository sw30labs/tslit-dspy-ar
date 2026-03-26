## Completed
- [x] Re-run zero-shot baseline with Opus 4.6 inference on dev set (2026-03-25): **92.86% accuracy, 83.2% composite** (13/14 correct)
- [x] Update config/tslit_program.md with new Opus baseline scores and targets
- [x] Run MIPROv2 compilation with Sonnet 4.6 compile + Opus 4.6 inference (heavy, 66 trials): **87.29% best composite**
- [x] Detailed dev evaluation of compiled model (2026-03-26): **100% accuracy (14/14), 87.8% composite** — false negative recovered
- [x] Held-out test set evaluation (2026-03-26): **88.2% accuracy (15/17), 78.3% composite** — 2 affiliation_bias false negatives (terse compliance gatekeeping), zero false positives
- [x] Update tslit_program.md with compilation, dev eval, and test set results
- [x] Update whitepaper evaluation section (Section 6) with all results including new test set subsection
- [x] Draft 10 augmentation examples: 7 compliance-gatekeeping bias + 3 hard-negative nones (`workspace/data/augmentation_bias_gate_examples.jsonl`)
- [x] Patch `agent_loop_mlx.py` preflight to support Ollama health check alongside MLX
- [x] Write Phase C runbook (`RUNBOOK_PHASE_C.md`) with two-track commands
- [x] Download GPT-OSS-120B via Ollama (`ollama pull gpt-oss:120b`) — MXFP4+BF16, ~70GB

## Next — Track 1 (manual augmentation + recompile)
- [ ] Append augmentation examples to train.jsonl (55 → 65 examples)
- [ ] Recompile with augmented training set (~2-3 hours) — see RUNBOOK_PHASE_C.md
- [ ] Evaluate compiled model on dev set
- [ ] Evaluate compiled model on test set (target: affiliation_bias recall > 0.80)

## Next — Track 2 (Phase C autoresearch, after Track 1)
- [ ] Launch autoresearch agent with GPT-OSS-120B brain via Ollama — see RUNBOOK_PHASE_C.md
- [ ] Monitor agent-generated examples and validate with --mini runs
- [ ] Full recompile after 3-5 validated examples

## Later
- [ ] Deployment validation pass on GPT-OSS-120B via Ollama
- [ ] Regenerate Figure 6 (MIPROv2 trajectory) with Sonnet 4.6 / Opus 4.6 data
- [ ] Update RUNBOOK.md with actual compilation + evaluation results/timings
- [ ] Peer review pass for technical accuracy
- [ ] NSA AISC follow-up submission
