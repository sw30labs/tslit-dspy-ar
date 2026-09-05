# TSLIT-HW GPU Observability Channel - Low-Level Design

**Status:** Design plus Phase 1 prototype scaffold. This file specifies the implementation contract for the prototype and later hardening work.
**Date:** 2026-04-28
**Primary deliverables:** `gpu_observability_api_surface.json`, `feature_extractor.py`, `hardware_detector.py`, and phase reports.

## 1. Canonical File Layout

Phase deliverables should use the exact filenames from the research brief at the `tslit_hw/` root so reviewers can find them quickly. Supporting code and data can live under subdirectories.

```text
tslit_hw/
|-- README-GPU-OBSERVABILITY.md
|-- gpu_observability_api_surface.json        # Phase 1 deliverable
|-- gpu_observability_api_findings.md         # Phase 1 500-word summary
|-- adversarial_gpu_signatures.md             # Phase 2 deliverable
|-- feature_extractor.py                      # Phase 2 deliverable
|-- hardware_detector.py                      # Phase 3 deliverable
|-- detection_results.md                      # Phase 3 deliverable
|-- calibration/
|   |-- qwen3_moe.kernelmap.json
|   `-- qwen3_moe.baseline.json
|-- capture/
|   |-- api_surface_probe.py
|   |-- cuda_binary_inventory.py
|   |-- cupti_harness.py
|   |-- dcgm_poller.py
|   |-- ioctl_tracer.py
|   |-- nvtx_calibrator.py
|   `-- trace_writer.py
|-- features/
|   |-- kernel_features.py
|   |-- ioctl_features.py
|   |-- memory_features.py
|   |-- moe_features.py
|   |-- occupancy_features.py
|   `-- timing_features.py
|-- detector/
|   |-- baseline.py
|   |-- rules.py
|   `-- report.py
|-- data/
|   |-- raw/
|   |   |-- cupti/
|   |   |-- dcgm/
|   |   `-- ioctl/
|   |-- manifests/
|   |-- derived/
|   `-- figures/
|-- cuda_binary_inventory.json
`-- design-gpu-observability/
    |-- DESIGN.md
    |-- HLD.md
    `-- LLD.md
```

The root-level `feature_extractor.py` and `hardware_detector.py` can be thin CLI wrappers over `features/*` and `detector/*`. This keeps the requested deliverables simple while preserving maintainable internals.

## 2. Runtime Assumptions

- Python 3.10 or newer, matching the existing project.
- Local CUDA 13.x toolkit with `libcupti.so`.
- NVIDIA driver version compatible with installed CUDA/CUPTI.
- DCGM installed and able to query the target GPU.
- Optional local-process IOCTL tracing through eBPF, `strace`, or an equivalent target-scoped mechanism.
- Optional `cuobjdump` and `readelf` tooling for static CUDA binary inventory.
- Single measured inference workload at a time.
- No cloud inference and no external API calls.
- Prompt text is optional in manifests; prompt hash is mandatory.

## 3. Phase 1 API Surface Probe

### 3.1 `capture/api_surface_probe.py`

Purpose: discover what is actually available on the target host and write `gpu_observability_api_surface.json`.

Proposed CLI:

```bash
python -m tslit_hw.capture.api_surface_probe \
  --out tslit_hw/gpu_observability_api_surface.json \
  --summary-out tslit_hw/gpu_observability_api_findings.md \
  --sample-ms 100 \
  --require-cupti \
  --require-dcgm \
  --enable-ioctl-smoke-test
```

Responsibilities:

- Detect OS, architecture, GPU name, GPU UUID, driver version, CUDA version, CUPTI library path, and DCGM version.
- Try the preferred CUPTI Python binding.
- If unavailable, verify that `libcupti.so` is loadable through `ctypes`.
- Enumerate CUPTI activity kinds needed by the design.
- Run a tiny CUDA workload and verify kernel/memcpy/memset/sync records can be captured.
- Query DCGM field availability and supported profiling metric groups.
- Probe static CUDA binary inspection tools such as `cuobjdump` and `readelf`.
- If requested, verify that local-process IOCTL metadata can be collected for NVIDIA device nodes without decoding payloads.
- Measure empty-workload overhead and one warm inference overhead if a model command is provided.

### 3.2 `gpu_observability_api_surface.json` Schema

```json
{
  "schema_version": "tslit_hw.api_surface.v1",
  "generated_at": "2026-04-28T00:00:00Z",
  "host": {
    "os": "Ubuntu 24.04",
    "arch": "aarch64",
    "kernel": "string"
  },
  "gpu": {
    "name": "string",
    "uuid": "string",
    "driver_version": "string",
    "cuda_version": "string",
    "compute_capability": "string"
  },
  "cupti": {
    "available": true,
    "binding": "cupti-python|ctypes|none",
    "libcupti_path": "string|null",
    "activity_kinds_enabled": [
      "CONCURRENT_KERNEL",
      "MEMCPY",
      "MEMSET",
      "SYNCHRONIZATION",
      "UNIFIED_MEMORY_COUNTER",
      "OVERHEAD",
      "MARKER",
      "MARKER_DATA"
    ],
    "capture_smoke_test": {
      "records_total": 0,
      "kernel_records": 0,
      "dropped_records": 0,
      "errors": []
    }
  },
  "dcgm": {
    "available": true,
    "version": "string",
    "sample_period_ms": 100,
    "fields_requested": [],
    "fields_available": [],
    "fields_unavailable": [],
    "profiling_group_notes": []
  },
  "ioctl": {
    "available": true,
    "tracer": "ebpf|strace|none",
    "scope": "local_process_only",
    "device_nodes_observed": [
      "/dev/nvidiactl",
      "/dev/nvidia0",
      "/dev/nvidia-uvm"
    ],
    "payload_decoding": false,
    "capture_smoke_test": {
      "records_total": 0,
      "unique_cmds": 0,
      "error_count": 0,
      "errors": []
    }
  },
  "overhead": {
    "empty_capture_ms": 0.0,
    "warm_inference_unmonitored_ms": null,
    "warm_inference_monitored_ms": null,
    "estimated_overhead_pct": null
  },
  "phase1_open_questions": {
    "cupti_python_aarch64_cuda13": "pass|fail|unknown",
    "dcgm_permissions_dgx_spark": "pass|fail|unknown",
    "ioctl_local_process_trace": "pass|fail|unknown",
    "qwen3_moe_license": "pass|fail|unknown",
    "warm_forward_overhead": "pass|fail|unknown",
    "unique_kernel_count_budget": "pass|fail|unknown"
  }
}
```

### 3.3 Phase 1 500-Word Summary

`gpu_observability_api_findings.md` should be short and operational:

- Environment tested.
- CUPTI binding result and fallback path.
- DCGM field availability and permission behavior.
- IOCTL tracing result if enabled, including whether `/dev/nvidia-uvm` activity was visible.
- One manually correlated kernel/operator example.
- Overhead measurement.
- Green/yellow/red recommendation for Phase 2.

## 4. Raw Artifact Contracts

### 4.1 Probe Manifest

One manifest per measured probe, stored under `tslit_hw/data/manifests/{probe_id}.json`.

```json
{
  "schema_version": "tslit_hw.probe_manifest.v1",
  "probe_id": "uuid-or-stable-slug",
  "run_group_id": "uuid",
  "created_at": "2026-04-28T00:00:00Z",
  "label": "benign|affiliation_bias|temporal_logic_bomb|combined",
  "scenario": "string",
  "affiliation": "string",
  "virtual_time": "YYYY-MM-DD",
  "prompt_sha256": "hex",
  "prompt_text_path": null,
  "model": {
    "model_id": "qwen3-moe-local",
    "weights_sha256": "optional",
    "quantization": "bf16|int8|int4|other",
    "tokenizer_sha256": "optional"
  },
  "generation": {
    "max_new_tokens": 50,
    "temperature": 0.0,
    "top_p": 1.0,
    "seed": 1234
  },
  "environment": {
    "gpu_uuid": "string",
    "driver_version": "string",
    "cuda_version": "string",
    "dcgm_version": "string"
  },
  "artifacts": {
    "cupti_ndjson": "tslit_hw/data/raw/cupti/{probe_id}.ndjson",
    "dcgm_ndjson": "tslit_hw/data/raw/dcgm/{probe_id}.ndjson",
    "ioctl_ndjson": null,
    "feature_json": "tslit_hw/data/derived/{probe_id}.features.json"
  }
}
```

### 4.2 CUPTI Activity NDJSON

Each line is one normalized activity record.

```json
{
  "schema_version": "tslit_hw.cupti_activity.v1",
  "probe_id": "string",
  "record_index": 1,
  "kind": "kernel|memcpy|memset|sync|uvm|overhead|marker|api|other",
  "ts_start_ns": 0,
  "ts_end_ns": 0,
  "duration_ns": 0,
  "device_id": 0,
  "context_id": "string|null",
  "stream_id": "string|null",
  "correlation_id": "string|null",
  "name": "raw-or-demangled-name",
  "kernel": {
    "grid": [1, 1, 1],
    "block": [1, 1, 1],
    "registers_per_thread": null,
    "static_smem_bytes": null,
    "dynamic_smem_bytes": null
  },
  "memory": {
    "bytes": null,
    "copy_kind": null,
    "src_kind": null,
    "dst_kind": null
  },
  "uvm": {
    "counter_kind": null,
    "value": null
  },
  "nvtx": {
    "range_id": null,
    "range_name": null
  },
  "operator": {
    "class": null,
    "layer_index": null,
    "match_rule_id": null
  }
}
```

### 4.3 DCGM NDJSON

```json
{
  "schema_version": "tslit_hw.dcgm_sample.v1",
  "probe_id": "string",
  "ts_us": 0,
  "gpu_id": 0,
  "fields": {
    "DCGM_FI_DEV_GPU_UTIL": 0.0,
    "DCGM_FI_DEV_MEM_COPY_UTIL": 0.0,
    "DCGM_FI_PROF_SM_ACTIVE": 0.0,
    "DCGM_FI_PROF_SM_OCCUPANCY": 0.0,
    "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE": 0.0,
    "DCGM_FI_PROF_PIPE_FP32_ACTIVE": 0.0,
    "DCGM_FI_PROF_PIPE_FP16_ACTIVE": 0.0,
    "DCGM_FI_PROF_DRAM_ACTIVE": 0.0,
    "DCGM_FI_PROF_PCIE_TX_BYTES": 0,
    "DCGM_FI_PROF_PCIE_RX_BYTES": 0,
    "DCGM_FI_PROF_NVLINK_TX_BYTES": 0,
    "DCGM_FI_PROF_NVLINK_RX_BYTES": 0,
    "DCGM_FI_DEV_POWER_USAGE": 0.0,
    "DCGM_FI_DEV_GPU_TEMP": 0.0,
    "DCGM_FI_DEV_SM_CLOCK": 0.0
  }
}
```

### 4.4 IOCTL NDJSON

IOCTL tracing is optional and intentionally metadata-only. Payload decoding is out of scope for Phase 1-3 because NVIDIA driver IOCTL structs are version-sensitive and often opaque.

```json
{
  "schema_version": "tslit_hw.ioctl_event.v1",
  "probe_id": "string",
  "event_index": 1,
  "ts_start_ns": 0,
  "ts_end_ns": 0,
  "duration_ns": 0,
  "pid": 0,
  "tid": 0,
  "comm": "python",
  "device_node": "/dev/nvidia-uvm",
  "fd": 12,
  "cmd_hex": "0x00000000",
  "cmd_group": "nvidiactl|gpu|uvm|unknown",
  "direction": "read|write|read_write|none|unknown",
  "arg_size_bytes": null,
  "return_code": 0,
  "errno": null,
  "notes": []
}
```

Recommended capture scope:

- Trace only the local inference process and child threads.
- Record `/dev/nvidiactl`, `/dev/nvidia*`, `/dev/nvidia-uvm`, and `/dev/nvidia-uvm-tools`.
- Do not record IOCTL payload bytes by default.
- Do not treat command numbers as stable semantics across driver versions.

## 5. Static CUDA Binary Inventory Contract

`cuda_binary_inventory.json` records static CUDA binary facts for the runtime before prompt execution:

```json
{
  "schema_version": "tslit_hw.cuda_binary_inventory.v1",
  "generated_at": "2026-04-28T00:00:00Z",
  "roots": ["/usr/local/cuda"],
  "tools": {
    "readelf": "/usr/bin/readelf",
    "cuobjdump": "/usr/local/cuda/bin/cuobjdump"
  },
  "summary": {
    "candidate_files": 0,
    "inspected_files": 0,
    "cuda_related_files": 0,
    "sm_arches": ["sm_120"],
    "compute_arches": ["compute_120"],
    "cuda_versions": ["13"]
  },
  "files": []
}
```

This is not a detector by itself. It is a static fingerprint to explain runtime traces, catch CUDA/runtime drift, and identify fatbin/cubin/PTX assets that may determine what kernels can execute.

## 6. Kernel Map Contract

`calibration/qwen3_moe.kernelmap.json` maps raw kernel signatures to operator classes.

```json
{
  "schema_version": "tslit_hw.kernelmap.v1",
  "model_id": "qwen3-moe-local",
  "created_at": "2026-04-28T00:00:00Z",
  "environment_fingerprint": {
    "driver_version": "string",
    "cuda_version": "string",
    "torch_version": "string",
    "transformers_version": "string",
    "quantization": "string"
  },
  "coverage": {
    "reference_runs": 10,
    "unique_kernel_names": 0,
    "matched_kernel_fraction": 0.0,
    "unknown_kernel_fraction": 0.0
  },
  "rules": [
    {
      "rule_id": "moe_expert_gemm_001",
      "kernel_name_regex": ".*gemm.*",
      "nvtx_range_regex": "tslit\\.op\\..*moe.*expert.*",
      "operator_class": "moe_expert_gemm",
      "expected_grid_shapes": [],
      "expected_layer_indices": []
    }
  ]
}
```

Operator classes:

- `gemm_qkv`
- `gemm_o_proj`
- `gemm_ffn_up`
- `gemm_ffn_down`
- `rmsnorm`
- `rope`
- `softmax`
- `moe_gate`
- `moe_scatter`
- `moe_expert_gemm`
- `moe_gather`
- `elementwise`
- `reduce`
- `copy`
- `sync`
- `other`
- `unknown`

Escalation: if unique kernel names exceed 500 in a warmed Qwen3 MoE reference run, stop before building a full regex table and decide whether to bucket by library/operator family instead.

## 7. Feature Vector Contract

One feature JSON per probe.

```json
{
  "schema_version": "tslit_hw.feature_vector.v1",
  "probe_id": "string",
  "label": "benign|affiliation_bias|temporal_logic_bomb|combined",
  "features": {
    "kernel_prefix_entropy": 0.0,
    "kernel_launches_per_token": 0.0,
    "unknown_kernel_fraction": 0.0,
    "sequence_edit_distance": 0.0,
    "routing_gini": null,
    "routing_kl_vs_benign": null,
    "per_layer_routing_skew_delta": null,
    "dram_bandwidth_z": 0.0,
    "interface_traffic_z": 0.0,
    "unified_memory_fault_rate": 0.0,
    "sm_occupancy_variance": 0.0,
    "tensor_pipe_active_fraction": 0.0,
    "p99_time_per_token_ms": 0.0,
    "inter_token_jitter_ms": 0.0,
    "ioctl_call_rate": null,
    "ioctl_unique_cmd_count": null,
    "ioctl_error_rate": null,
    "uvm_ioctl_burst_score": null,
    "telemetry_expected_samples": 0,
    "telemetry_observed_samples": 0,
    "telemetry_sample_ratio": null,
    "telemetry_max_sample_gap_ms": null,
    "telemetry_missing_metric_count": null,
    "telemetry_poller_failure_count": 0,
    "telemetry_health_warning_count": 0
  },
  "quality": {
    "cupti_records": 0,
    "dcgm_samples": 0,
    "ioctl_events": 0,
    "dropped_cupti_records": 0,
    "matched_kernel_fraction": 0.0,
    "measurement_overhead_pct": null,
    "monitor_health": {
      "poller_count": 0,
      "poller_started_count": 0,
      "nvidia_smi_poller_started": false,
      "missing_metrics": []
    }
  }
}
```

Null is allowed for MoE-specific features during Phase 1 or when the target is not an MoE model. Null is also allowed for IOCTL features when tracing is disabled. Telemetry-health fields are not detector evidence by themselves; they decide whether the detector evidence is trustworthy. Phase 3 detector rules must reject a MoE-routing rule if those fields are null, and must treat IOCTL-only findings as audit warnings unless Phase 2 explicitly promotes them.

## 8. Telemetry Health Contract

Phase 1 must answer two questions for every measured probe: what did the GPU do, and did our monitors stay healthy while it did it?

The prototype runner writes these health fields into `features.csv`, `features.json`, and each probe manifest:

| Field | Meaning |
|---|---|
| `telemetry_expected_samples` | Expected `nvidia-smi` samples from measured duration and requested sample period |
| `telemetry_observed_samples` | Actual parsed `nvidia-smi` rows |
| `telemetry_sample_ratio` | Observed / expected samples, null if the poller was unavailable |
| `telemetry_max_sample_gap_ms` | Largest parsed timestamp gap in the monitor stream |
| `telemetry_missing_metric_count` | Count of requested `nvidia-smi` columns missing from the captured CSV |
| `telemetry_poller_failure_count` | Pollers that failed to start or exited before the measured command ended |
| `telemetry_health_warning_count` | Coarse warning count combining poller failures, low sample ratio, missing metrics, and large gaps |

Interpretation rule: if a benign/adversarial delta appears only in runs with telemetry-health warnings, report it as capture instability until repeated clean runs reproduce the effect.

## 9. `feature_extractor.py`

### 8.1 Proposed CLI

```bash
python tslit_hw/feature_extractor.py \
  --manifest tslit_hw/data/manifests/{probe_id}.json \
  --kernel-map tslit_hw/calibration/qwen3_moe.kernelmap.json \
  --benign-baseline tslit_hw/calibration/qwen3_moe.baseline.json \
  --out tslit_hw/data/derived/{probe_id}.features.json
```

Batch mode:

```bash
python tslit_hw/feature_extractor.py \
  --manifest-dir tslit_hw/data/manifests \
  --kernel-map tslit_hw/calibration/qwen3_moe.kernelmap.json \
  --benign-baseline tslit_hw/calibration/qwen3_moe.baseline.json \
  --out-dir tslit_hw/data/derived
```

### 8.2 Public Functions

```python
def load_cupti_trace(path: Path) -> list[dict]:
    """Load normalized CUPTI NDJSON records."""

def load_dcgm_samples(path: Path) -> list[dict]:
    """Load normalized DCGM NDJSON records."""

def load_ioctl_events(path: Path | None) -> list[dict]:
    """Load optional normalized IOCTL NDJSON events."""

def annotate_operators(records: list[dict], kernel_map: dict) -> list[dict]:
    """Attach operator class and layer hints from regex/NVTX rules."""

def extract_features(
    manifest: dict,
    cupti_records: list[dict],
    dcgm_samples: list[dict],
    ioctl_events: list[dict] | None,
    kernel_map: dict,
    benign_baseline: dict | None = None,
) -> dict:
    """Return a tslit_hw.feature_vector.v1 object."""
```

### 8.3 Feature Computation Notes

- `kernel_prefix_entropy`: Shannon entropy over `(operator_class, layer_index_mod_block)` tokens.
- `kernel_launches_per_token`: kernel record count divided by generated token count.
- `unknown_kernel_fraction`: `unknown / total_kernel_records`.
- `sequence_edit_distance`: normalized Levenshtein distance from median benign operator sequence.
- `routing_gini`: inferred from per-expert GEMM shapes where possible.
- `routing_kl_vs_benign`: KL divergence against benign routing centroid, smoothed with epsilon.
- `per_layer_routing_skew_delta`: max absolute deviation from benign per-layer Gini.
- `dram_bandwidth_z`: z-score from DCGM `DCGM_FI_PROF_DRAM_ACTIVE`.
- `interface_traffic_z`: z-score over PCIe/NVLink byte deltas.
- `unified_memory_fault_rate`: UVM counter events per second or per generated token.
- `sm_occupancy_variance`: variance of `DCGM_FI_PROF_SM_OCCUPANCY` over measured window.
- `tensor_pipe_active_fraction`: fraction of samples where tensor pipe active exceeds configured threshold.
- `p99_time_per_token_ms`: p99 token interval, derived from token NVTX ranges if available; otherwise run wall time divided by tokens is a fallback, not p99.
- `inter_token_jitter_ms`: standard deviation of token intervals.
- `ioctl_call_rate`: IOCTL events per second during the measured inference window.
- `ioctl_unique_cmd_count`: number of distinct command numbers observed during the measured window.
- `ioctl_error_rate`: fraction of IOCTL events returning non-zero or errno.
- `uvm_ioctl_burst_score`: maximum rolling count of `/dev/nvidia-uvm` IOCTLs in a short window, normalized by generated tokens.

## 10. Baseline Contract

`calibration/qwen3_moe.baseline.json` freezes benign statistics.

```json
{
  "schema_version": "tslit_hw.baseline.v1",
  "model_id": "qwen3-moe-local",
  "created_at": "2026-04-28T00:00:00Z",
  "probe_count": 30,
  "feature_names": [],
  "primary_feature_names": [],
  "auxiliary_feature_names": [
    "ioctl_call_rate",
    "ioctl_unique_cmd_count",
    "ioctl_error_rate",
    "uvm_ioctl_burst_score"
  ],
  "mean": {},
  "std": {},
  "covariance": [],
  "median_operator_sequence": [],
  "routing_centroids": {},
  "environment_fingerprint": {}
}
```

Minimum baseline requirements:

- 30 warmed benign probes.
- Prompt lengths within +/- 10 percent of the target budget where possible.
- No concurrent GPU workloads.
- Same driver, CUDA, DCGM, model weights, quantization, and generation settings as measured adversarial runs.
- Same IOCTL tracing mode if IOCTL features are included in Phase 2 analysis.

## 11. `hardware_detector.py`

### 10.1 Proposed CLI

```bash
python tslit_hw/hardware_detector.py \
  --features tslit_hw/data/derived/{probe_id}.features.json \
  --baseline tslit_hw/calibration/qwen3_moe.baseline.json \
  --thresholds tslit_hw/calibration/qwen3_moe.thresholds.json \
  --out tslit_hw/data/derived/{probe_id}.hardware_detection.json
```

Evaluation mode:

```bash
python tslit_hw/hardware_detector.py \
  --features-dir tslit_hw/data/derived \
  --baseline tslit_hw/calibration/qwen3_moe.baseline.json \
  --labels tslit_hw/data/labels.jsonl \
  --write-report tslit_hw/detection_results.md
```

### 10.2 Detection Output Schema

```json
{
  "schema_version": "tslit_hw.detection.v1",
  "probe_id": "string",
  "is_adversarial": false,
  "confidence": 0.0,
  "tier": "dcgm_fast_filter|cupti_deep_scan|none",
  "reason": "single-sentence mechanical explanation",
  "scores": {
    "mahalanobis_dcgm": 0.0,
    "mahalanobis_all": 0.0,
    "moe_routing_score": null,
    "ioctl_audit_score": null
  },
  "top_contributors": [
    {
      "feature": "unknown_kernel_fraction",
      "z_score": 0.0,
      "direction": "above|below"
    }
  ],
  "quality": {
    "accepted": true,
    "warnings": []
  },
  "auxiliary_warnings": []
}
```

### 10.3 Rule Shape

Tier 1: DCGM-only fast filter.

```text
dcgm_features = [
  dram_bandwidth_z,
  interface_traffic_z,
  sm_occupancy_variance,
  tensor_pipe_active_fraction
]

tier1_flag = mahalanobis(dcgm_features, benign_dcgm_centroid) > tau1
```

Tier 2: CUPTI deep scan.

```text
all_feature_flag = mahalanobis(primary_available_features, benign_centroid) > tau2
moe_flag = routing_kl_vs_benign > tau_kl and per_layer_routing_skew_delta > tau_skew

is_adversarial = all_feature_flag or moe_flag
```

IOCTL handling:

```text
ioctl_audit_flag = (
  ioctl_error_rate > tau_ioctl_error
  or uvm_ioctl_burst_score > tau_uvm_burst
  or ioctl_unique_cmd_count > tau_ioctl_unique
)

# Phase 3 default:
# ioctl_audit_flag adds warnings and corroboration only.
# It does not set is_adversarial unless Phase 2 explicitly shows that
# an IOCTL feature is separable, stable, and mechanically explainable.
```

Confidence:

- Initial Phase 3: monotonic transform of distance above threshold.
- Optional only if data supports it: 2-component Gaussian mixture posterior.

Reason:

- Select the accepted feature with the largest absolute z-score contribution.
- Render as: "`{feature}` is `{z}` standard deviations `{above|below}` benign baseline."
- If MoE rule fires, prefer a routing-specific reason over a generic Mahalanobis reason.
- If only IOCTL audit features fire, return a warning-style reason such as "driver-boundary IOCTL pattern drifted from baseline" and keep `is_adversarial` false by default.

## 12. Threshold Selection

`qwen3_moe.thresholds.json`:

```json
{
  "schema_version": "tslit_hw.thresholds.v1",
  "created_at": "2026-04-28T00:00:00Z",
  "selection_method": "grid_search_f1_with_latency_secondary",
  "tau1": 0.0,
  "tau2": 0.0,
  "tau_kl": 0.0,
  "tau_skew": 0.0,
  "tau_ioctl_error": null,
  "tau_uvm_burst": null,
  "tau_ioctl_unique": null,
  "operating_point": {
    "tpr": 0.0,
    "fpr": 0.0,
    "precision": 0.0,
    "recall": 0.0,
    "f1": 0.0,
    "latency_overhead_pct": 0.0
  },
  "heldout_split": {
    "train_probe_ids": [],
    "validation_probe_ids": [],
    "test_probe_ids": []
  }
}
```

No threshold should be hand-tuned on the final test probes. If the dataset is too small to support a split, report Phase 3 as exploratory and do not make operational claims.

## 13. Phase 2 Signature Report

`adversarial_gpu_signatures.md` should include:

- Trace counts by label.
- Environment fingerprint.
- Static CUDA binary inventory summary: CUDA-related files, SM targets, compute targets, CUDA version hints.
- Telemetry-health table: sample ratio, max sample gap, missing metric count, and poller failures by label.
- Feature table by class: mean, std, median, min, max.
- Separate primary-vs-auxiliary feature table, with IOCTL features clearly marked as auxiliary.
- Pairwise benign/adversarial deltas.
- Mann-Whitney U or equivalent non-parametric test where sample size permits.
- Scatter plot references.
- Null results, including features that failed to separate.
- Decision on whether Phase 3 is justified.

Suggested table:

```markdown
| Feature | Benign mean | Adv mean | Delta | Effect | p-value | Notes |
|---|---:|---:|---:|---:|---:|---|
| unknown_kernel_fraction | 0.02 | 0.08 | +0.06 | medium | 0.04 | kernel-map drift |
```

## 14. Phase 3 Results Report

`detection_results.md` should include:

- Dataset split and exact probe IDs.
- ROC/AUC and selected operating point.
- Confusion matrix.
- Latency overhead table: unmonitored, DCGM-only, CUPTI deep-scan.
- Telemetry-health summary for every evaluated mode; detector results are not valid if health warnings dominate.
- IOCTL overhead table if IOCTL tracing was enabled: unmonitored, `strace`/eBPF tracing, CUPTI+DCGM+IOCTL combined.
- Per-class results for benign, affiliation bias, temporal logic bomb, and combined.
- One paragraph explaining why the rule works or why it failed.
- Known false positives and false negatives.
- Recommendation: promote to whitepaper result, demote to appendix, or stop.

## 15. Error Handling and Stop Conditions

Stop before Phase 2 if:

- CUPTI cannot capture kernel records on the target machine.
- DCGM profiling fields are unavailable and no replacement signal is available.
- Monitor-health warnings dominate baseline runs, making capture instability larger than model/runtime signal.
- Warm monitored inference overhead exceeds 10 percent and the only separating features are timing features.
- No kernel can be correlated to any model operator.
- Qwen3 MoE weights cannot be used under a license compatible with the research.

Do not stop before Phase 2 merely because IOCTL tracing is unavailable. Record it as an unsupported auxiliary channel and proceed with CUPTI/DCGM if the primary gates pass.

Stop before Phase 3 if:

- Benign and adversarial feature distributions do not separate visually or statistically.
- Feature quality warnings dominate the runs.
- The detector explanation depends on vague language such as "looks different" without a concrete mechanism.

## 16. Minimal Test Plan

Unit tests:

- NDJSON loaders reject malformed lines with useful errors.
- Kernel-map regex rules annotate expected sample records.
- Feature functions handle empty traces, missing DCGM fields, and null MoE features.
- IOCTL feature functions handle missing IOCTL files by returning null auxiliary features.
- Telemetry-health functions handle empty monitor CSVs, missing columns, unparseable timestamps, and poller start failures.
- Mahalanobis scoring handles singular covariance through regularization.

Integration tests on target hardware:

- Tiny CUDA workload emits at least one kernel record.
- DCGM samples requested fields at 100 ms or documents the nearest supported cadence.
- Monitor-health fields report expected/observed samples and max sample gap for each measured probe.
- Warm-up runs are excluded from measured traces.
- Generated feature vector is stable across three identical benign probes within a configured tolerance.
- Optional IOCTL smoke test captures at least one NVIDIA device-node IOCTL during a tiny CUDA workload, or records why tracing is unavailable.
- Static binary inventory scan completes on `/usr/local/cuda` or records why tooling/paths are unavailable.

Research verification:

- Phase 1 manual kernel/operator correlation.
- Phase 2 scatter plot review.
- Phase 3 single-paragraph rule explanation.

## 17. Vendor and External Tool Posture

NVIDIA CUPTI/DCGM are the primary implementation surfaces. Nsight Systems may be used as a calibration cross-check but should not be required for normal capture. IOCTL/eBPF tracing is secondary and should be used to compare driver-boundary behavior, UVM activity, and nonstandard runtime behavior.

Stealthium or any other external observability product can be evaluated later as an optional comparison channel. It should not receive raw prompts, raw traces, employer-sensitive context, or design-partner framing without a separate decision. Current user-provided Stealthium notes suggest SIEM/o11y exports are available while customer-authored live detectors remain roadmap, so the Phase 3 detector must be implemented locally. IOCTL/eBPF support in TSLIT-HW gives a local comparison point for vendor claims about kernel/driver telemetry.

## 18. Open Questions Preserved from the Brief

1. Is `cupti-python` or a usable `libcupti.so` path available for aarch64 CUDA 13.x on GB10?
2. Does DCGM profiling group access work on DGX Spark without special permissions?
3. Is the intended Qwen3 MoE checkpoint licensed for this use?
4. What is measured CUPTI Activity API overhead on a warmed Qwen3 forward pass?
5. How many unique kernels appear in a warmed Qwen3 MoE run, and does that count exceed the 500-kernel regex budget?
6. Can local-process IOCTL tracing observe NVIDIA device-node and UVM activity on DGX Spark without unacceptable privileges or overhead?
