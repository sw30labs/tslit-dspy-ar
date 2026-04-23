# tslit_hw — GPU Observability Channel (design phase)

Sibling package to `tslit_dspy/`. Explores a hardware-level adversarial-detection channel using NVIDIA CUPTI (kernel activity) and DCGM (SM occupancy, DRAM/NVLink, pipe utilisation) during inference on adversary-origin MoE models.

**Current state:** design-only. No code, no empirical claims. Single artifact: `design/DESIGN.md`.

**Target hardware:** DGX Spark (GB10 Grace-Blackwell, CUDA 12.x, Ubuntu 24.04).
**Target model:** Qwen3 MoE.
**Companion to:** `tslit_dspy/` application-layer pipeline.

## Status

- [x] Phase 1 escalation: sandbox cannot execute; design-doc-first path chosen.
- [x] API surface mapped (CUPTI sub-APIs, DCGM field groups, Python binding candidates).
- [x] Kernel-to-operator mapping strategy defined (two-stage: NVTX calibration → regex table).
- [x] 14-feature vector pre-registered (kernel / MoE / memory / occupancy / timing).
- [x] Two-tier decision rule shape specified (DCGM fast filter + CUPTI deep scan).
- [x] Phase-gate green-light criteria defined for Phase 1 → 2 → 3.
- [x] Honest limitations section (overhead contamination, unified-memory architecture, attacker-aware padding, kernel-map drift).
- [ ] Red-team pass on design doc.
- [ ] Phase 1 execution on DGX Spark (blocked on red-team + silicon access).

## Read next

Start with `design/DESIGN.md`. Section 13 ("Red-team prompts for the reader") is the intended entry point for reviewers.
