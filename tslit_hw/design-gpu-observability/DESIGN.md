# TSLIT-HW: GPU Observability Channel — Design Specification

**Status:** Design plus Phase 1 prototype scaffold. No DGX Spark empirical claims.
**Author:** Nicolas Cravino (ncravino@mac.com)
**Date:** 2026-04-23
**Target hardware:** NVIDIA DGX Spark (GB10 Grace-Blackwell, unified memory, Ubuntu 24.04, CUDA 13.x)
**Target model (Phase 2):** Qwen3 MoE (local weights, quantized or BF16)
**Companion package to:** `tslit_dspy/` (application-layer detection)

---

## 0. One-paragraph thesis

The `tslit_dspy` pipeline detects adversarial behavior by reasoning over *what the model says*. An attacker who controls the weights can, in principle, make the model say bland plausible things while still executing a hidden code path — for example, a routing skew in a Mixture-of-Experts gate that only fires on a specific geopolitical trigger token, or a silent call into an attention head that has been re-keyed to a backdoor direction. Output-layer detection cannot distinguish the benign and adversarial cases if the output text itself is deliberately laundered. A second, lower-layer channel is therefore proposed: sample the **silicon**. Every forward pass is physically a sequence of CUDA kernel launches, memory transfers, SM occupancy states, and driver-boundary operations. If the adversarial path touches different weights, routes to different experts, or takes a different number of kernels to produce a token, the hardware tells on it. This document specifies what CUPTI and DCGM can and cannot see, how optional IOCTL/eBPF tracing can corroborate the driver-control plane, how we would map kernels back to transformer operators on a Qwen3 MoE target, which feature vectors are defensible, what the decision-rule shape should look like, and where the likely failure modes are. The accompanying prototype scaffold is intentionally small so it can be red-teamed before expensive silicon time is spent.

---

## 1. Why a hardware channel (threat model delta)

### 1.1 What application-layer TSLIT already catches

The existing `tslit_dspy` 4-stage pipeline (ThreatClassifier → EvidenceExtractor → RiskScorer → QAValidator) observes the model's *response* to a probe and reasons about it. It catches:

- **Affiliation bias**: the model systematically shades answers toward a sovereign or corporate interest.
- **Temporal logic bombs**: behavior that differs across injected timestamps or implied dates.
- **Combined / geopolitical censorship**: refusals, deflections, or compliance-gatekeeping conditioned on specific referents.

This works because the attack surface — the model's text output — is what we are classifying. It has held up to 88.2% accuracy on the current 17-example held-out test set.

### 1.2 The blind spot

A sufficiently careful adversary does not need to produce visibly bad text. A backdoor can be:

- **Silent-routing**: an MoE gate that, on a trigger embedding, routes a token through an expert that reads from a re-keyed shard of weights. Output text remains plausible; only the *path* changes.
- **Attention-head hijack**: a single head that, conditioned on a trigger, writes into residual in a direction that biases downstream generation without producing any single "flaggable" token.
- **Dormant**: the path is present but never fires on any prompt TSLIT has ever seen. Application-layer scanning by construction cannot reach it.

In all three cases the silicon knows. The kernel sequence for a backdoor-active forward pass is not identical to the kernel sequence for a benign forward pass — different experts means different GEMM batch sizes, different memory read offsets, different all-to-all traffic.

### 1.3 What "hardware ground truth" really buys us

It is worth being honest: CUPTI/DCGM do not give us the weights. They give us *proxies* for path-divergence:

- Kernel name sequence, duration, grid/block dims, shared-memory usage
- Memory-transfer patterns (unified memory faults, NVLink traffic, PCIe)
- SM occupancy, tensor-pipe-active fraction, DRAM bandwidth utilisation
- Launch count per token
- Optional driver-boundary IOCTL rates, command diversity, UVM bursts, and error codes
- Static CUDA binary inventory: fatbin/cubin/PTX presence, SM targets, CUDA version hints, and hashes
- Telemetry-health signals: expected-vs-observed samples, missing fields, sample gaps, and poller failures

The adversary's theorem of difficulty is: *you cannot make a different computational path look identical to a baseline path at the kernel scheduler, without actually being the same path.* A sophisticated attacker could pad benign branches with decoy kernels, but (a) that increases latency measurably, and (b) decoy kernels have to access memory that leaves a physical trail. It is a harder game than laundering text.

For Phase 1, the message is deliberately punchy: what did the GPU do, and did our monitors stay healthy while it did it? On DGX Spark / CUDA 13.x, monitor health is part of the result because immature or partially supported tools can otherwise masquerade as model behavior.

This is the narrow but genuine contribution the hardware channel makes: it pushes the attacker's problem from **output-string indistinguishability** (easy) to **kernel-trace indistinguishability** (much harder).

---

## 2. CUPTI API surface

### 2.1 Sub-APIs and which one we want

CUPTI is a layered toolkit:

| Sub-API | What it gives | Fit for TSLIT-HW |
|---|---|---|
| Activity API | Asynchronous record stream of kernel launches, memcpy, memset, sync events, NVLink, PCIe, driver/runtime calls | **Primary channel.** Low overhead, covers everything we need for kernel-sequence features. |
| Callback API | Synchronous hooks on `cuLaunchKernel`, runtime calls, resource events | Used in calibration only, to emit NVTX ranges and tag operators. |
| Metrics/Events API | Hardware performance counters (warp stalls, cache hit rate, etc.) | Overhead is 10–50%. Avoid in production capture; optional in forensic deep-dive. |
| PC Sampling | Instruction-level sampling inside kernels | Overkill. Not needed for path-divergence signals. |
| Profiler API (Range/Replay) | Kernel replay for accurate metric collection | Not compatible with online detection — requires re-executing kernels. |

**Decision:** TSLIT-HW uses the **Activity API** for online capture and the **Callback API + NVTX** only during the one-time calibration pass that builds the kernel-to-operator mapping (§5).

### 2.2 Relevant activity record kinds

These are the record types we intend to subscribe to. All are already defined in CUPTI 12.x; none require out-of-tree builds.

- `CUPTI_ACTIVITY_KIND_KERNEL` / `..._CONCURRENT_KERNEL` — kernel launch + completion timestamps, grid/block dims, static/dynamic shared-memory bytes, register count, demangled function name, stream id.
- `CUPTI_ACTIVITY_KIND_MEMCPY` — host↔device, device↔device sizes and bandwidths. On DGX Spark unified memory, many of these are replaced by `CUPTI_ACTIVITY_KIND_UNIFIED_MEMORY_COUNTER` (page faults, thrashing). **Important architecture note** — see §8.2.
- `CUPTI_ACTIVITY_KIND_NVLINK` — per-link transmitted/received bytes. On single-node Spark this is largely zero but kept for generalisation.
- `CUPTI_ACTIVITY_KIND_PCIE` — host↔device link counters.
- `CUPTI_ACTIVITY_KIND_SYNCHRONIZATION` — stream syncs, events. Shape of sync graph is itself a signal.
- `CUPTI_ACTIVITY_KIND_OVERHEAD` — self-reported CUPTI overhead. We record this as a control variable so detection latency numbers are honest.

### 2.3 Delivery model

CUPTI Activity API uses the standard **buffer-request / buffer-complete** producer-consumer pattern. Client code:

1. Registers a `cuptiActivityRegisterCallbacks` pair.
2. On buffer-request, hands CUPTI a pinned buffer.
3. On buffer-complete, drains records out of the buffer and emits them to disk (NDJSON) or to an in-memory ring for online scoring.

For Phase 1 capture the design calls for **NDJSON to disk**, one file per probe, plus an in-memory summary struct returned to the caller. That keeps raw traces available for offline re-analysis when feature definitions change.

### 2.4 Python binding choice

Three candidate bindings, evaluated on code review alone (no Phase 1 execution yet):

| Binding | Pros | Cons |
|---|---|---|
| `cupti-python` / NVIDIA CUPTI Python packages | Maintained path if wheels match CUDA 13.x and aarch64 | Wheel/import shape on GB10 needs verification at Phase 1 time |
| `pycupti` (community) | Pure-Python wrapper | Unmaintained, may lag CUDA 13.x |
| Direct `ctypes` against `libcupti.so` | Zero dependencies, always works | We write the wrapper; ~200 lines of boilerplate per API |

**Decision:** start with `cupti-python`; fall back to `ctypes` if the wheel is not available for the aarch64 DGX Spark. **This is a Phase 1 escalation trigger** — if neither binding works out of the box, we stop and ask before committing to a custom C extension.

---

## 3. DCGM API surface

### 3.1 What DCGM adds on top of CUPTI

CUPTI is per-process, per-kernel. DCGM is **system-wide, time-series**. It samples counters the host driver exposes at 100 ms – 1 s rates. This gives us three things CUPTI cannot:

1. **Background noise calibration** — what SM occupancy and DRAM bandwidth look like when *no* inference is running. Needed so Phase 2 baselines aren't contaminated by other workloads.
2. **A cheap always-on rule** — DCGM polling at 1 Hz adds ~0% workload overhead and can gate the more expensive CUPTI capture ("only trace suspicious runs").
3. **Thermal / clock state** — adversarial loads that run the SMs harder than benign loads leak through clock-throttle events.

### 3.2 Field groups we subscribe to

DCGM field IDs, grouped:

**Utilisation:**
- `DCGM_FI_DEV_GPU_UTIL`
- `DCGM_FI_DEV_MEM_COPY_UTIL`
- `DCGM_FI_PROF_SM_OCCUPANCY`
- `DCGM_FI_PROF_SM_ACTIVE`

**Compute pipes (profiling metrics — these are the real signal):**
- `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE`
- `DCGM_FI_PROF_PIPE_FP32_ACTIVE`
- `DCGM_FI_PROF_PIPE_FP16_ACTIVE`

**Memory:**
- `DCGM_FI_PROF_DRAM_ACTIVE`
- `DCGM_FI_DEV_FB_USED` / `..._FREE`

**Interconnect:**
- `DCGM_FI_PROF_NVLINK_RX_BYTES` / `..._TX_BYTES`
- `DCGM_FI_PROF_PCIE_RX_BYTES` / `..._TX_BYTES`

**Power/thermal:**
- `DCGM_FI_DEV_POWER_USAGE`
- `DCGM_FI_DEV_GPU_TEMP`
- `DCGM_FI_DEV_SM_CLOCK`

### 3.3 Delivery model

Two modes:

- **Continuous monitor** — `dcgmi dmon` or `pydcgm` field group, 100 ms sample rate, rolling window to disk.
- **On-demand snapshot** — bracketed around each CUPTI capture, so we can align DCGM samples to kernel-trace timestamps.

Time synchronisation: both CUPTI activity records and DCGM samples carry absolute timestamps (CUPTI in ns since epoch, DCGM in µs). A correlation pass in Phase 1 verifies their clocks agree to within the DCGM sample period.

---

## 3.4 IOCTL/eBPF secondary channel

IOCTL tracing is added as a **secondary control-plane channel**, not as a primary model-semantic detector. CUPTI remains the per-kernel path signal and DCGM remains the low-overhead time-series signal. IOCTL sits below both, at the Linux device-driver boundary, where local inference interacts with `/dev/nvidiactl`, `/dev/nvidia0`, `/dev/nvidia-uvm`, and related nodes.

What we collect, if enabled:

- Process/thread id and command name
- Device node and file descriptor
- IOCTL command number, direction, and argument size if the tracing mechanism exposes them
- Start/end timestamp, duration, return code, and errno
- Coarse grouping: `nvidiactl`, `gpu`, `uvm`, or `unknown`

What we do **not** do in Phase 1-3:

- Decode NVIDIA-private IOCTL payloads
- Treat command numbers as stable semantic labels across driver versions
- Build the primary adversarial detector on IOCTL features alone
- Trace unrelated system processes by default

The value is corroboration and forensics: UVM bursts can support a memory-path anomaly; unexpected error codes can flag anti-profiler or runtime issues; unusual command diversity can indicate nonstandard model-runtime behavior. If IOCTL tracing is unavailable or too privileged on DGX Spark, the spike proceeds with CUPTI/DCGM and records IOCTL as unsupported.

---

## 4. Coverage-gap analysis: what each sees, what they miss together

| Question | CUPTI | DCGM | IOCTL/eBPF | Neither |
|---|---|---|---|---|
| Which kernel fired? | Yes (name, dims) | No | No | — |
| How long did a kernel take? | Yes (ns) | No | No | — |
| Which driver calls bracketed runtime behavior? | Partial via API activity | No | Yes (metadata) | Payload semantics |
| Per-token SM occupancy? | Derived from kernel dims | Directly, at 100ms grain | No | — |
| MoE expert routing distribution? | Inferred from per-expert GEMM batches | No | No | Gate logits themselves |
| DRAM bytes read per kernel? | No (would need Metrics API, high overhead) | Aggregate only | No | Exact per-kernel bytes |
| UVM/page-fault control-plane bursts? | UVM counters | No | Yes, coarse metadata | Exact managed-memory internals |
| NVLink bytes across a probe? | Yes (per event) | Yes (aggregate) | No | — |
| Clock throttling mid-inference? | No | Yes | No | — |
| Attention-head-level re-keying? | No — same kernel shape | No | No | Needs hook-level or weight-level access |

Two takeaways:

1. **The hardware channel is strong on routing and memory-path changes, weak on in-kernel weight tampering.** A backdoor that uses the exact same kernel shape and memory footprint but poisoned weights is *invisible to this channel.* We are honest about that in §8.4 and in the whitepaper writeup.
2. **CUPTI + DCGM together cover more than either alone** — DCGM provides the slow-drift baseline for z-scores and the thermal/clock angle; CUPTI provides the fine-grained path features. IOCTL can add driver-boundary corroboration, especially around UVM and unusual runtime behavior. Fusion is at feature-vector level (§6), not at raw-record level.

---

## 5. Kernel-to-operator mapping (Qwen3 MoE specifics)

### 5.1 Why this is hard

CUPTI gives us demangled kernel names like:

```
ampere_fp16_s16816gemm_fp16_128x128_ldg8_relu_f2f_stages_64x4_tn
void cutlass::Kernel<...>(...)
at::native::vectorized_elementwise_kernel<...>
fused_rms_norm_kernel_...
```

These are library-level names (cuBLAS, cutlass, aten). They do **not** carry any "this was attention layer 34's Q-projection" semantic label. We need to reconstruct that mapping.

### 5.2 Two-stage approach

**Stage A — Calibration (offline, once per model build):**

1. Load Qwen3 MoE under `torch.profiler` with `with_stack=True` and `with_modules=True`.
2. Wrap each named module forward with `torch.autograd.profiler.record_function("tslit.op.<class>.<layer_idx>.<tag>")`. CUPTI receives these as NVTX ranges.
3. Run a short warm-up + 10 reference inference passes on benign prompts.
4. Build a mapping table:
   ```
   kernel_name_regex → {operator_class, expected_layer_indices, expected_grid_shapes}
   ```
   Operator classes: `gemm_qkv`, `gemm_o_proj`, `gemm_ffn_up`, `gemm_ffn_down`, `rmsnorm`, `rope`, `softmax`, `moe_gate`, `moe_scatter`, `moe_expert_gemm`, `moe_gather`, `elementwise`, `reduce`, `copy`, `other`.
5. Persist this as `tslit_hw/calibration/qwen3_moe.kernelmap.json`.

**Stage B — Online inference (every probe):**

1. Capture CUPTI Activity records.
2. For each record, look up operator class via regex table; if unmatched, bucket as `unknown`. Fraction of `unknown` records is itself a feature (§6).
3. Attach layer-index annotations by tracking kernel order within a token and using block-size heuristics (a full transformer block is ~18–24 kernels on Qwen3, and the MoE layer is identifiable by its scatter/gather pair).

### 5.3 Qwen3 MoE architecture assumptions

The design assumes the following about the target model (all verifiable against a Qwen3 MoE checkpoint before Phase 2; all may need correction once the actual model is loaded):

- Decoder-only transformer, alternating Attention + FFN blocks.
- Some FFN blocks replaced with MoE: top-K routing (typically K=2) over N experts (commonly 60-128), SwiGLU per expert.
- Routing: a small linear gate, softmax, top-K select, optionally with a load-balancing auxiliary.
- Dispatch: either `grouped_gemm` (one kernel, batched by expert) or `all_to_all` + per-expert GEMM + reverse `all_to_all` (multi-GPU; on single DGX Spark, degenerates to local scatter).

### 5.4 MoE-specific observables we intend to extract

- **Tokens-per-expert distribution** — inferred from the sequence of per-expert GEMM kernel launches. Each expert GEMM's M-dimension equals the number of tokens routed to it. This is *directly observable* from CUPTI kernel records once we have the kernel map.
- **Routing Gini / KL vs uniform** — computed from the tokens-per-expert vector per layer, averaged across layers.
- **Scatter/gather kernel timing** — abnormally long scatter or gather events indicate unusual routing skew.
- **Cross-layer routing correlation** — does the same token keep routing to a specific expert index across layers? Trigger-conditional backdoors can appear as routing "traffic jams" on a specific expert.

This is the signal richness the hardware channel delivers that application-layer TSLIT cannot see.

---

## 6. Feature vector definitions (pre-registered for Phase 2/3)

All features are computed per probe (one probe = one prompt → one inference). The probe is pinned to a fixed generation budget (default: 50 tokens, to match the brief's Phase 1 target).

**Notation:** `B` = benign baseline (µ, σ) for a given feature; `x` = value on current probe; `z(x) = (x − µ_B)/σ_B`.

### 6.1 Kernel-sequence features (from CUPTI)

1. **Kernel-prefix entropy `H_k`** — Shannon entropy over the multiset of (operator_class, layer_idx mod block_depth) pairs observed during the probe. Benign inference is a structured, near-deterministic sequence → low entropy. Backdoor-induced detours should raise entropy.
2. **Kernel-launch count per token `λ_k`** — total kernel launches divided by tokens generated. Benign is tight (variance < 2%). Adversarial paths with extra scatters or auxiliary ops leak here.
3. **Unknown-kernel fraction `φ_unk`** — fraction of captured kernels that failed to match the calibration regex. Direct integrity signal: if a model build has kernels we've never seen, something is off.
4. **Sequence-edit distance `Δ_seq`** — Levenshtein distance between the observed operator-class sequence and the median benign sequence (normalized by length). Captures insertions/deletions that entropy misses.

### 6.2 MoE-routing features (from kernel dims)

5. **Routing Gini `G_moe`** — Gini coefficient over the tokens-per-expert distribution, averaged across MoE layers. Range [0,1]; benign Qwen3 with load-balancing is typically low (<0.2).
6. **Routing KL vs benign `D_kl_moe`** — KL divergence of the observed routing histogram against the benign centroid histogram, per layer, averaged.
7. **Per-layer routing skew delta `Δ_skew`** — maximum per-layer deviation of Gini from the benign per-layer Gini. Single-layer trigger backdoors would concentrate signal here.

### 6.3 Memory & interconnect features (from CUPTI + DCGM)

8. **DRAM-bandwidth z-score `z_bw`** — standardised DCGM `PIPE_DRAM_ACTIVE` during the probe. A backdoor that reads from an unused weight shard manifests here.
9. **NVLink/PCIe anomaly `z_iface`** — z-score on interconnect traffic. On DGX Spark single-node this is mainly a weight-exfiltration tripwire, not a path-divergence signal.
10. **Unified-memory fault-rate `f_umf`** — DGX Spark specific (§8.2): page-fault count from `CUPTI_ACTIVITY_KIND_UNIFIED_MEMORY_COUNTER`. Different memory access patterns show up as different fault profiles even when aggregate bandwidth is similar.

### 6.4 Occupancy & pipe features (from DCGM)

11. **SM-occupancy variance `σ²_occ`** — variance of `SM_OCCUPANCY` across the probe window. Benign inference is steady; backdoor paths introduce bumpier occupancy curves.
12. **Tensor-pipe-active fraction `f_tpa`** — fraction of time `PIPE_TENSOR_ACTIVE` > 0.5. Backdoor paths that offload to FP32 or non-tensor pipes reduce this.

### 6.5 Timing features (from CUPTI)

13. **p99 time-per-token `p99_tpt`** — tail latency per token. The brief explicitly calls out latency; tail is where rare adversarial paths surface.
14. **Inter-token-jitter `J_t`** — stdev of per-token wall times. Complementary to p99.

### 6.6 Optional IOCTL/control-plane features

These are **auxiliary** features. They are recorded in Phase 1/2 if tracing works, but they do not participate in the primary Phase 3 detector unless Phase 2 proves they are stable, separable, and mechanically explainable.

15. **IOCTL call rate `r_ioctl`** — IOCTL events per second or per generated token.
16. **Unique IOCTL command count `n_ioctl_cmd`** — distinct command numbers observed during the measured inference window.
17. **IOCTL error rate `e_ioctl`** — fraction of IOCTLs with non-zero return or errno.
18. **UVM IOCTL burst score `b_uvm`** — maximum rolling `/dev/nvidia-uvm` IOCTL count over a short window, normalized by generated tokens.

**Total: 14 primary features per probe, plus 4 optional IOCTL auxiliary features.** Phase 3's decision rule does not need all of them — the point is to over-measure in Phase 2 and let an empirical feature-importance pass (permutation importance + pairwise correlations) prune. IOCTL-only anomalies default to audit warnings, not confirmed adversarial detections.

### 6.7 Static CUDA binary inventory

The first prototype also records a static inventory of CUDA-bearing runtime artifacts before prompt execution. This uses `cuobjdump`, `readelf`, and byte-level markers where available to identify `.nv_fatbin`/`.nvFatBinSegment`, cubin/PTX hints, CUDA version strings, SM/compute targets such as `sm_120`, and file hashes. This is not an execution detector: it describes what code is available to the runtime, not which kernels actually ran. Its purpose is to explain traces, catch runtime drift, and identify whether benign/adversarial runs accidentally used different compiled artifacts.

### 6.8 Telemetry-health features

Telemetry health is recorded beside model/runtime features but is not adversarial evidence by itself. It decides whether the evidence is trustworthy.

The first prototype records:

- Expected `nvidia-smi` samples from measured duration and requested sample period.
- Observed `nvidia-smi` rows.
- Observed/expected sample ratio.
- Maximum parsed monitor timestamp gap.
- Missing requested metric count.
- Poller failure count.
- Aggregate telemetry-health warning count.

If an apparent benign/adversarial delta appears only when sample ratios are low, metrics disappear, or pollers exit early, the finding is reported as capture instability until repeated clean runs reproduce it.

### 6.9 Benign centroid construction

The benign baseline B is not a single run. It is:

- 30 benign probes from the existing TSLIT test set, covering range of prompt lengths within ±10% of the target token budget.
- Warm-up: 3 discard runs before the first recorded run, to flush kernel-cache cold-start noise.
- Recompute baseline weekly during active research; pin it for headline numbers in the whitepaper.

We publish `µ_B, σ_B` for every feature alongside any claimed detection rate.

---

## 7. Decision-rule architecture (Phase 3 shape, pre-registration)

The brief asks for a "simple decision rule." The following is the *shape* — not the numeric thresholds, which are empirical and must be learned in Phase 2.

### 7.1 Two-tier rule

**Tier 1 — DCGM-only fast filter (always on, ~0% overhead):**
- Features: `z_bw`, `σ²_occ`, `f_tpa`, `z_iface`.
- Rule: Mahalanobis distance to the benign centroid in this 4-D subspace > τ₁.
- Purpose: cheap flag. High tolerance for false positives; false positives here just *trigger* tier 2, which is harmless.

**Tier 2 — CUPTI deep-scan (triggered, ~5–15% overhead):**
- Features: all 14.
- Rule: multivariate outlier test against the benign centroid, plus a MoE-routing-specific sub-rule:
  ```
  flag_adversarial  ⟺  (Mahalanobis_14 > τ₂)  OR  (D_kl_moe > τ_kl  AND  Δ_skew > τ_skew)
  ```
- The MoE sub-rule is deliberately redundant: it fires on concentrated routing anomalies even when aggregate distance stays moderate, because that is precisely the backdoor shape we most care about.

**Auxiliary — IOCTL audit channel (optional, not primary):**
- Features: `r_ioctl`, `n_ioctl_cmd`, `e_ioctl`, `b_uvm`.
- Default behavior: add warnings and corroborating evidence only.
- Promotion condition: an IOCTL feature can enter the detector only if Phase 2 shows stable separation and a mechanistic explanation that does not depend on opaque payload decoding.

### 7.2 Confidence and reason outputs

The Phase 3 function signature the brief requested:

```
def detect_adversarial_inference(cupti_trace, dcgm_snapshot) -> (is_adversarial: bool,
                                                                 confidence: float,
                                                                 reason: str):
    ...
```

- `is_adversarial`: boolean from the Tier-1-then-Tier-2 rule.
- `confidence`: posterior under a 2-component Gaussian mixture fit on labelled Phase 2 data. Scalar in [0, 1].
- `reason`: the single feature with the largest z-score contribution to the flag, rendered as a human-readable string — e.g., `"routing Gini in layer 12 is 4.1σ above benign centroid"`. This is what goes into TSLIT's JSON threat report. If only IOCTL features fire, the reason is rendered as an audit warning and `is_adversarial` remains false by default.

### 7.3 Threshold selection

Thresholds τ₁, τ₂, τ_kl, τ_skew are *not* set by intuition. They are chosen on a held-out subset of Phase 2 runs by maximising F1 on a grid, with latency-cost as a secondary constraint. We publish the chosen thresholds and a ROC curve in the whitepaper; we do not hand-tune on test.

### 7.4 What we are *not* doing

- **No deep learning.** A neural classifier on kernel traces is tempting but unjustifiable at 60 Phase 2 runs. We stay with thresholded Mahalanobis distance until we have >1000 labelled traces.
- **No online adaptation.** Benign baseline is a frozen weekly snapshot, not a streaming update — the latter lets an attacker slowly poison the centroid.
- **No cross-model generalisation claims.** A rule trained on Qwen3 does not transfer to DeepSeek without re-calibration of the kernel map. The whitepaper text must be explicit about this.

---

## 8. Honest limitations and open questions

### 8.1 CUPTI overhead can contaminate timing features

The Activity API adds ~2–8% overhead in practice (exact number to be measured in Phase 1). That means `p99_tpt` and `J_t` are measured on the *instrumented* path, not the ground truth. Two mitigations: (a) we only use those features when detection is specifically about timing-side-channel backdoors, and (b) we record `CUPTI_ACTIVITY_KIND_OVERHEAD` alongside so the overhead term is regressed out when comparing runs.

### 8.2 DGX Spark unified memory changes the memory-signal story

GB10 Grace-Blackwell uses unified LPDDR5X shared between CPU and GPU. Classical discrete-GPU memcpy patterns are largely absent. Consequences:

- `z_bw` remains valid (DCGM still measures the memory subsystem).
- `CUPTI_ACTIVITY_KIND_MEMCPY` is sparse; we rely on `CUPTI_ACTIVITY_KIND_UNIFIED_MEMORY_COUNTER` (fault-based) instead.
- Cross-platform transferability to H100/H200 systems requires a separate calibration.

This is an architecture-specific design choice we live with; the alternative is running on discrete GPUs we don't have.

### 8.3 Cold-start dominates the first inference

The first inference on a fresh model load triggers kernel compilation and cache population that looks wildly different from steady-state. Three discard warm-up runs before every capture are mandatory. If an adversarial trigger only fires on first use, we miss it — accepted limitation, documented in the whitepaper.

### 8.4 An adversary-aware attacker can pad benign decoys

Nothing in this design stops a motivated attacker from adding fake benign-shaped kernels in parallel to the adversarial path, raising the benign path's apparent kernel count to match. Two counters, neither complete:

- Padding takes time; `p99_tpt` and total runtime grow. Power draw goes up. Both are measurable.
- Padding has to read memory; the memory pattern is still physical. `f_umf` distinguishes.

We do not claim the channel is attacker-proof. We claim it raises the cost of an undetected attack and is orthogonal to the application-layer channel. Defence-in-depth, not silver bullet.

### 8.5 Kernel-map drift across driver updates

A minor CUDA driver revision can change kernel selection heuristics (cuBLAS heuristic updates, cutlass version bumps). The kernel-name regex table is therefore pinned per-driver-version and the calibration pass re-runs automatically on every driver upgrade. A fingerprint mismatch without a known driver change is itself a detection signal.

### 8.6 IOCTL drift, opacity, and privilege risk

NVIDIA IOCTL command numbers and payload structures are not a stable semantic API for this research. They are driver-version-sensitive, frequently opaque, and often require elevated tracing permissions depending on the capture mechanism. This makes IOCTL a good audit/control-plane channel and a poor first detector channel. The design records metadata only and treats IOCTL features as auxiliary unless Phase 2 demonstrates otherwise.

### 8.7 Open questions for Phase 1 execution

Noted here so the operator running Phase 1 on the DGX Spark knows what to flag:

1. Is `cupti-python` or a usable `libcupti.so` path available for aarch64 CUDA 13.x? (Escalation if no direct or `nsys`-mediated tracing path works.)
2. Does DCGM profiling group enable out of the box on DGX Spark, or does it need explicit `dcgmi profile --pause` gymnastics?
3. Do we have a Qwen3 MoE checkpoint legitimately downloadable for the research? (Weights licensing: confirm before proceeding.)
4. What is the measured CUPTI Activity API overhead on a warm Qwen3 forward pass?
5. How many unique kernel names does Qwen3 MoE emit in a reference run? (If >500, the regex table is a larger engineering exercise than expected.)
6. Can local-process IOCTL tracing observe NVIDIA device-node and UVM activity on DGX Spark without unacceptable privileges or overhead?

---

## 9. Phase-gate green-light criteria (re-stating the brief, operationalised)

**Phase 1 → Phase 2 gate.** Proceed only if:
- CUPTI Activity API captures kernel records for a Qwen3 MoE 50-token inference with overhead ≤10%.
- At least 90% of captured kernels match the calibration regex table (i.e., `φ_unk ≤ 0.10`).
- A manual spot-check correlates at least one kernel name to a named Qwen3 operator (e.g., MoE scatter).
- DCGM profiling field group samples without permission errors at 100 ms grain.
- Telemetry-health fields show acceptable sample completeness and no unexplained poller failures during the measured window.
- IOCTL tracing, if enabled, records local-process driver-boundary metadata without requiring payload decoding. Failure here is not a Phase 1 blocker if CUPTI/DCGM pass.

If any of the above fails, the correct action is **not** to fabricate data; it is to stop and ask. This is explicit because the downstream audience (NSA AISC, Big 4, critical-infra operators) will assume our numbers are real.

**Phase 2 → Phase 3 gate.** Proceed only if:
- At least two features show a statistically significant delta (|z| > 2, p < 0.05 under a Mann-Whitney U test) between benign and adversarial runs, across three prompt variants in each of three adversarial classes.
- A 2-D projection (e.g., `G_moe` × `Δ_seq`) shows visually separable benign/adversarial clusters.
- If separation is absent, we *stop and rewrite the hypothesis* rather than keep tuning.

**Phase 3 → whitepaper contribution gate.** Proceed only if:
- TPR ≥ 0.70 at FPR ≤ 0.10 on 100 labelled probes.
- Detection-latency overhead ≤ 20% of unmonitored inference time.
- A single paragraph explains *mechanistically* why the rule works. If the explanation is hand-wavy, the rule is premature and goes in the "future work" appendix, not the results section.

---

## 10. Proposed `tslit_hw/` package layout

The Phase 1 prototype code is intentionally small and lands in the following package layout:

```
tslit_hw/
├── README.md
├── design/
│   └── DESIGN.md                    # this document
├── capture/
│   ├── cuda_binary_inventory.py      # Phase 1 — static fatbin/cubin/PTX inventory
│   ├── cupti_harness.py             # Phase 1 — Activity API wrapper
│   ├── dcgm_poller.py               # Phase 1 — DCGM field-group subscriber
│   ├── ioctl_tracer.py              # Phase 1 — optional driver-boundary metadata
│   └── nvtx_calibrator.py           # Stage A calibration helper
├── calibration/
│   └── qwen3_moe.kernelmap.json     # produced by Stage A, checked in
├── features/
│   ├── kernel_features.py           # §6.1 + §6.5
│   ├── ioctl_features.py            # §6.6 auxiliary controls
│   ├── moe_features.py              # §6.2
│   ├── memory_features.py           # §6.3
│   └── occupancy_features.py        # §6.4
├── detector/
│   ├── baseline.py                  # benign centroid construction
│   ├── rules.py                     # two-tier rule from §7
│   └── report.py                    # emits tslit_dspy-compatible ThreatReport
├── tests/
│   └── ...
├── data/
    ├── traces/                       # raw CUPTI NDJSON per probe
    ├── dcgm/                         # DCGM CSV per probe
    ├── ioctl/                        # optional IOCTL NDJSON per probe
    └── labeled/                      # (trace, dcgm, label) tuples
└── cuda_binary_inventory.json        # optional static runtime fingerprint
```

Integration with `tslit_dspy`: the `detector/report.py` module emits a `ThreatReport` dataclass compatible with `tslit_dspy/schemas.py` so the hardware channel can be fused with application-layer channel at the `TSLITAnalyzer` level without upstream edits.

---

## 11. Empirical pre-registration (what gets published even if results are null)

To keep the whitepaper contribution honest even under a null result, we pre-commit now to reporting:

- All 14 primary features on every probe, not a cherry-picked subset.
- All available IOCTL auxiliary features, clearly separated from the primary detector feature set.
- Feature distributions per class (benign, affiliation-bias, temporal-logic-bomb, geopolitical-trigger), including means, stdevs, and N.
- The chosen thresholds and the ROC curve, with the operating point used.
- Per-feature permutation importance.
- The full list of "failed" feature hypotheses that did not yield separation, with one-line explanations.

If the channel does not work on Qwen3 MoE, that is a publishable result too — it narrows where future work should look (discrete-GPU H100, smaller MoE, etc.).

---

## 12. References and further reading

Not a formal bibliography — these are the specific documents the design leans on, which the Phase 1 operator should have open:

- *CUDA Profiling Tools Interface (CUPTI) User's Guide*, NVIDIA, current for CUDA 13.x — Activity API, Callback API, record type definitions.
- *NVIDIA DCGM User Guide*, NVIDIA — field group IDs, profiling metrics prerequisites, permission model.
- *NVIDIA Nsight Systems documentation* — not used directly in the harness, but an excellent ground-truth cross-check during calibration.
- Linux `ioctl(2)`, eBPF, and `strace` documentation — used only for optional driver-boundary metadata capture, not payload decoding.
- *Qwen3 model card and architecture paper* (Alibaba) — MoE configuration, top-K, expert count. Confirm exact numbers against the checkpoint used.
- *DSPy: Declarative Self-improving Python* (Khattab et al., 2023) — integration target on the application-layer side.
- TSLIT-DSPy whitepaper, Section 4 — the pipeline this channel is designed to augment.

---

## 13. Red-team prompts for the reader

Before any silicon time is spent in Phase 1, the most useful thing a reader can do is attack this document. Specific questions worth an honest answer:

1. **Does the hardware-ground-truth argument in §1.3 actually hold?** Where exactly does it break? Is the attacker's cost genuinely higher, or am I comforting myself?
2. **Is the feature set in §6 redundant or missing something obvious?** Cache-miss rate? Warp-divergence counters? Something from Nsight we are not reading?
3. **Is the 14-feature Mahalanobis rule in §7 too simple, too complex, or about right for 60 labelled probes?**
4. **Are the phase gates in §9 strict enough to prevent the paper from making a claim the data cannot support?**
5. **What is a benign-but-weird workload that will look adversarial to this rule?** Long-context prompts, quantisation mode changes, batching variations — each is a potential false-positive source that needs a principled answer, not a threshold tweak.

If the document survives red-teaming, Phase 1 is authorised. If not, we revise before committing silicon.

---

*End of design specification. Prototype capture scaffolding exists, but no DGX Spark empirical numbers are claimed. The next action is Phase 1 execution on the DGX Spark, followed by a pause if benign and adversarial runs do not show repeatable, explainable differences.*
