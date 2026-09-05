# tslit_hw — GPU Observability Channel

Sibling package to `tslit_dspy/`. Explores a hardware-level adversarial-detection channel using NVIDIA CUPTI (kernel activity), DCGM (SM occupancy, DRAM/NVLink, pipe utilisation), and optional IOCTL/eBPF driver-boundary telemetry during inference on adversary-origin MoE models.

**Current state:** Phase 1 prototype scaffold. No DGX Spark empirical claims yet.

**Target hardware:** DGX Spark (GB10 Grace-Blackwell, CUDA 13.x, Ubuntu 24.04).
**Target model:** Qwen3 MoE.
**Companion to:** `tslit_dspy/` application-layer pipeline.

## Status

- [x] Phase 1 escalation: sandbox cannot execute; design-doc-first path chosen.
- [x] API surface mapped (CUPTI sub-APIs, DCGM field groups, Python binding candidates).
- [x] IOCTL added as a secondary/control-plane audit channel, not a primary detector surface.
- [x] Kernel-to-operator mapping strategy defined (two-stage: NVTX calibration → regex table).
- [x] 14 primary features pre-registered (kernel / MoE / memory / occupancy / timing), with optional IOCTL corroboration features.
- [x] Two-tier decision rule shape specified (DCGM fast filter + CUPTI deep scan).
- [x] Telemetry-health features added: Phase 1 asks what the GPU did and whether the monitors stayed healthy while it did it.
- [x] Phase-gate green-light criteria defined for Phase 1 → 2 → 3.
- [x] Honest limitations section (overhead contamination, unified-memory architecture, attacker-aware padding, kernel-map drift).
- [x] High-level design added (`design-gpu-observability/HLD.md`).
- [x] Low-level implementation contract added (`design-gpu-observability/LLD.md`).
- [x] First prototype runner, API probe, static CUDA binary inventory, and runbook added.
- [ ] Red-team pass on design doc.
- [ ] Phase 1 execution on DGX Spark (blocked on red-team + silicon access).

## Read next

Start with:

1. `RUNBOOK_SPARK_PROTOTYPE.md` to run the first DGX Spark falsification prototype.
2. `design-gpu-observability/HLD.md` for architecture, phase gates, and the Stealthium boundary.
3. `design-gpu-observability/LLD.md` for file layout, schemas, CLIs, feature contracts, and detector rules.
4. `design-gpu-observability/DESIGN.md` for the original long-form research specification. Section 13 ("Red-team prompts for the reader") is the intended entry point for reviewers.
