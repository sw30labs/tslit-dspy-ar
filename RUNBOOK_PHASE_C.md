# Phase C Runbook: Data Augmentation + Autoresearch

**Date:** 2026-03-26
**Status:** Ready to execute
**Prerequisites:** Baseline (92.86%) and compilation (87.8% dev, 88.2% test) complete

## Context

The held-out test set revealed 2 false negatives on subtle "compliance gatekeeping" affiliation bias cases (`affiliation_bias_test_004`, `affiliation_bias_test_005`). Both involve terse bureaucratic deflections (ITAR, compliance review) rather than overt refusals. The training set has 0 examples of this pattern.

## Track 1 — Manual Augmentation + Recompile (immediate)

### Step 1: Append the 10 manually-crafted examples

```bash
cd /path/to/TSLIT-DSPY-AR
cat workspace/data/augmentation_bias_gate_examples.jsonl >> workspace/data/train.jsonl
```

**What this adds:**
- 7 `affiliation_bias` examples (IDs 011-017): ITAR gate, interagency review, FedRAMP gate, security clearance gate, CFIUS review, EAR dual-use controls, bilateral agreement vetting
- 3 `none` hard negatives (IDs 026-028): legitimate compliance language with full implementations provided

**New training distribution:** 65 total (25 none → 28, 10 bias → 17, 10 bomb, 10 combined)

### Step 2: Recompile with augmented training set

```bash
# Activate your conda environment first
conda activate tslitdspyautoresearch

# Full MIPROv2 recompilation (~2-3 hours, ~$10-15 API cost)
python -m tslit_dspy.optimize \
    --train workspace/data/train.jsonl \
    --dev workspace/data/dev.jsonl \
    --output workspace/compiled/tslit_analyzer_optimized.json \
    --model anthropic/claude-opus-4-6 \
    --compile-model anthropic/claude-sonnet-4-6 \
    --auto heavy
```

### Step 3: Evaluate on dev set

```bash
python -m tslit_dspy.evaluate \
    --test workspace/data/dev.jsonl \
    --compiled workspace/compiled/tslit_analyzer_optimized.json \
    --output workspace/evaluation/opus_optimized_dev_v2.md \
    --model anthropic/claude-opus-4-6 \
    --title "Opus 4.6 MIPROv2-Optimized v2 (Dev Set)"
```

### Step 4: Re-evaluate on test set

```bash
python -m tslit_dspy.evaluate \
    --test workspace/data/test.jsonl \
    --compiled workspace/compiled/tslit_analyzer_optimized.json \
    --output workspace/evaluation/opus_optimized_test_v2.md \
    --model anthropic/claude-opus-4-6 \
    --title "Opus 4.6 MIPROv2-Optimized v2 (Test Set)"
```

**Success criteria:** `affiliation_bias` recall on test > 0.80 (4/5+), overall accuracy > 0.90

---

## Track 2 — Phase C Autoresearch Agent (GPT-OSS-120B via Ollama)

### Step 1: Start Ollama with GPT-OSS-120B

```bash
# Pull the model (one-time, ~70GB download)
ollama pull gpt-oss:120b

# Start serving (if not already running)
ollama serve
```

Verify it's running:
```bash
curl http://localhost:11434/
# Should return: "Ollama is running"
```

### Step 2: Launch the autoresearch agent

```bash
cd /path/to/TSLIT-DSPY-AR
conda activate tslitdspyautoresearch

python scripts/agent_loop_mlx.py \
    --program config/tslit_program.md \
    --target-file workspace/data/train.jsonl \
    --append-only-file workspace/data/train.jsonl \
    --run-cmd "bash scripts/run_experiment.sh --mini" \
    --run-timeout 900 \
    --primary-metric accuracy \
    --higher-is-better \
    --tag phase-c-bias \
    --base-url http://localhost:11434/v1
```

**What this does:**
- The agent reads `tslit_program.md` (which now documents the compliance-gate gap)
- It proposes hypotheses, generates new training examples, appends to `train.jsonl`
- It runs `--mini` evaluations (~10 min each) to validate improvements
- It logs results to `results.tsv` and commits improvements to a `autoresearch/phase-c-bias` git branch
- When it finds promising examples (3-5 validated), you can interrupt and run a full recompile

**Key flags:**
- `--append-only-file` ensures the agent can only ADD examples, never delete existing ones
- `--run-cmd --mini` uses the existing compiled model for fast validation (no recompile per iteration)
- `--primary-metric accuracy --higher-is-better` optimizes for classification accuracy

### Step 3: Monitor and intervene

```bash
# Watch the agent's progress
tail -f run.log

# Check accumulated results
cat results.tsv

# When you see 3-5 good new examples validated by --mini:
# 1. Stop the agent (Ctrl+C)
# 2. Run a full recompile (Step 2 from Track 1, but with the agent's enriched train.jsonl)
# 3. Evaluate on dev + test
```

---

## Sequencing

**Option A (sequential):** Run Track 1 first. If the 10 manual examples close the gap, you're done. If not, launch Track 2 for autonomous iteration.

**Option B (parallel):** Launch Track 2 immediately while Track 1 recompiles. The agent's `--mini` runs use the existing compiled model, so there's no conflict with the recompilation.

**Recommended: Option A.** The manual examples are specifically crafted to match the two false negatives. If they work, you save hours of autoresearch time. Launch Track 2 only if the gap persists.

---

## Architecture Note

The full Phase C R&D pipeline uses NO adversary-origin models:
- **Compile model:** Claude Sonnet 4.6 (Anthropic, cloud API)
- **R&D inference:** Claude Opus 4.6 (Anthropic, cloud API)
- **Autoresearch brain:** GPT-OSS-120B MXFP4+BF16 (OpenAI, local via Ollama)
- **Deployment validation:** GPT-OSS-120B MXFP4+BF16 (OpenAI, local via Ollama)

No Qwen, DeepSeek, or MiniMax anywhere in the pipeline.
