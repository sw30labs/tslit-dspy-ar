# DGX Spark Prototype Runbook

This is the first falsification-oriented prototype for the TSLIT-HW thesis. It does not assume the DGX Spark stack is mature. It probes the machine, runs a tiny adversarial/non-adversarial dataset, and records whatever telemetry is available.

Phase 1 has two questions:

1. What did the GPU do?
2. Did our monitors stay healthy while it did it?

On DGX Spark / CUDA 13.x, the second question is first-class. Missing utilities, dropped samples, disappearing metrics, stalled pollers, and large sample gaps are not just harness annoyances; they are evidence about whether this observability channel is mature enough to support Phase 2.

## 1. Probe the Machine

From the repository root:

```bash
python -m tslit_hw.capture.api_surface_probe \
  --out tslit_hw/gpu_observability_api_surface.json \
  --summary-out tslit_hw/gpu_observability_api_findings.md \
  --sample-ms 100 \
  --enable-ioctl-smoke-test
```

Read:

- `tslit_hw/gpu_observability_api_surface.json`
- `tslit_hw/gpu_observability_api_findings.md`

On DGX Spark / CUDA 13.x, treat missing utilities as data. The prototype can still run with only command timing and `nvidia-smi`; DCGM, `nsys`, CUPTI libraries, and `strace` are opportunistic channels.

## 2. Static CUDA Binary Inventory

The Stealthium fatbin writeup suggests a useful static companion signal: what CUDA code is available to the runtime before any prompt executes. Scan likely runtime paths before the dynamic experiment:

```bash
python -m tslit_hw.capture.cuda_binary_inventory \
  --path /usr/local/cuda \
  --path "$HOME/.cache" \
  --out tslit_hw/cuda_binary_inventory.json \
  --max-files 5000
```

If you know where your inference runtime stores compiled extensions, add it explicitly. Good candidates are the Python environment `site-packages`, vLLM/Triton cache directories, Ollama model/runtime directories, and any local build output that contains `.so`, `.cubin`, `.fatbin`, or `.ptx` files.

The inventory records CUDA-looking files, `.nv_fatbin` markers, SM/compute targets, CUDA version hints, `cuobjdump --list-elf` output when available, and SHA-256 hashes for reasonably sized files. This does not prove adversarial behavior, but it helps explain kernel traces and catches runtime drift between benign and adversarial runs.

## 3. Prepare a Local Model Command

The runner expects a command that reads a prompt file and writes a response file. It provides these placeholders:

- `{prompt_file}`
- `{output_file}`
- `{probe_id}`
- `{label}`
- `{probe_dir}`

Example wrapper command shape:

```bash
python local_infer.py --prompt-file {prompt_file} --output-file {output_file}
```

If your model runner reads stdin and writes stdout, create a tiny adapter script rather than forcing shell redirection into the harness. Keeping the prompt and response as files makes artifacts easier to inspect.

## 4. Smoke Test Without the Model

This verifies the harness on any machine:

```bash
python -m tslit_hw.prototype_runner \
  --dataset tslit_hw/data/sample_probe_dataset.jsonl \
  --out-dir tslit_hw/runs \
  --run-id dry-run-smoke \
  --dry-run \
  --repeats 1 \
  --sample-ms 100 \
  --collect-binary-inventory \
  --binary-scan-path tslit_hw
```

Expected outputs:

- `tslit_hw/runs/dry-run-smoke/api_surface.json`
- `tslit_hw/runs/dry-run-smoke/features.csv`
- `tslit_hw/runs/dry-run-smoke/prototype_results.md`
- `tslit_hw/runs/dry-run-smoke/cuda_binary_inventory.json`
- `tslit_hw/runs/dry-run-smoke/probes/*/manifest.json`

## 5. First Real DGX Spark Run

Start conservative: one repeat, no `nsys`, no IOCTL, only low-risk polling.

```bash
python -m tslit_hw.prototype_runner \
  --dataset tslit_hw/data/sample_probe_dataset.jsonl \
  --out-dir tslit_hw/runs \
  --run-id spark-baseline-001 \
  --command-template "python local_infer.py --prompt-file {prompt_file} --output-file {output_file}" \
  --repeats 1 \
  --warmups 1 \
  --timeout 300 \
  --sample-ms 100 \
  --collect-dcgm \
  --collect-binary-inventory \
  --binary-scan-path /usr/local/cuda
```

Then inspect `prototype_results.md` and `features.csv`. If benign/adversarial groups already overlap everywhere, that is early pressure against the thesis for this model/runtime.

Before reading any apparent adversarial delta, check the telemetry-health fields in `features.csv`:

- `telemetry_expected_samples`
- `telemetry_observed_samples`
- `telemetry_sample_ratio`
- `telemetry_max_sample_gap_ms`
- `telemetry_missing_metric_count`
- `telemetry_poller_failure_count`
- `telemetry_health_warning_count`

## 6. Wider Capture Run

If the baseline run works, add `nsys` and IOCTL tracing:

```bash
python -m tslit_hw.prototype_runner \
  --dataset tslit_hw/data/sample_probe_dataset.jsonl \
  --out-dir tslit_hw/runs \
  --run-id spark-wide-capture-001 \
  --command-template "python local_infer.py --prompt-file {prompt_file} --output-file {output_file}" \
  --repeats 3 \
  --warmups 1 \
  --timeout 600 \
  --sample-ms 100 \
  --collect-dcgm \
  --collect-nsys \
  --collect-ioctl \
  --collect-binary-inventory \
  --binary-scan-path /usr/local/cuda \
  --binary-scan-path "$HOME/.cache"
```

Notes:

- `--collect-nsys` can add overhead but may produce the most useful CUDA trace if direct CUPTI Python bindings are not ready.
- `--collect-ioctl` uses `strace` if available. It records metadata only and remains auxiliary.
- `--collect-binary-inventory` is static and should be low risk. It is useful for explaining CUDA 13 fatbins, PTX fallback, and architecture target drift.
- If either tool fails on DGX Spark, leave the failed logs in place. That is part of the Phase 1 API surface result.

## 7. Decision Rule for the Spike

After the first real run, ask:

- Do adversarial labels show repeatable timing, utilization, `nsys`, DCGM, IOCTL, or static runtime-inventory differences?
- Did monitor health remain stable across benign and adversarial probes?
- Are differences larger than normal run-to-run variance?
- Can at least one difference be explained mechanically?

If the answer is no across repeated runs, pause before building Phase 3. That is exactly the falsification path this prototype is meant to expose.
