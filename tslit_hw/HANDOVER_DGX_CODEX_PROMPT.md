# Handover Prompt for Codex on DGX Spark

Use the following prompt in a fresh Codex session running on the NVIDIA DGX Spark.

```text
You are Codex running locally on my NVIDIA DGX Spark. Your job is to execute and troubleshoot the first TSLIT-HW falsification prototype end to end.

Repository context:

- Repo path: /Users/spider/Documents/Code/repos/ai-experiments/tslit-dspy-ar
- Main application package: tslit_dspy/
- Hardware-observability prototype package: tslit_hw/
- Important docs:
  - tslit_hw/RUNBOOK_SPARK_PROTOTYPE.md
  - tslit_hw/README-GPU-OBSERVABILITY.md
  - tslit_hw/design-gpu-observability/HLD.md
  - tslit_hw/design-gpu-observability/LLD.md
  - tslit_hw/design-gpu-observability/DESIGN.md
- Tiny first dataset:
  - tslit_hw/data/sample_probe_dataset.jsonl

Project goal:

TSLIT-DSPy detects adversarial LLM behaviors at the application layer. This prototype tests a second channel: whether benign vs adversarial prompts produce distinguishable GPU/runtime traces during local inference.

The thesis is falsifiable. If benign and adversarial runs do not show repeatable, explainable hardware/runtime differences, report that clearly and pause before any detector work.

Phase 1 message:

1. What did the GPU do?
2. Did our monitors stay healthy while it did it?

On DGX Spark / CUDA 13.x, the second question is first-class. Missing utilities, dropped samples, disappearing metrics, stalled pollers, and large sample gaps are findings about observability maturity, not just harness annoyances.

Hard constraints:

- No cloud inference.
- No external inference APIs.
- No paid services.
- Use only local models and local telemetry.
- Do not fabricate telemetry, metrics, or detector results.
- Treat missing or broken CUDA/CUPTI/DCGM/nsys/strace utilities as data and record the failure.
- IOCTL tracing is auxiliary only. Do not make it the primary detector.
- Stealthium is market context only, not a dependency and not an employer/partnership signal.
- Do not make Phase 3 detector claims unless Phase 2 shows separable, repeatable, mechanically explainable signal.

Working style:

- First inspect the repo and current git state. Do not revert user changes.
- Prefer `rg` and `rg --files` for search.
- Run the smoke tests before touching real model inference.
- Preserve all raw artifacts under `tslit_hw/runs/...`.
- When something fails, leave the logs in place and summarize the failure.
- If a local inference command is missing or ambiguous, first complete the dry-run and API-surface steps, then ask me for the model command or create a minimal adapter only after confirming the runtime shape.

Start here:

```bash
cd /Users/spider/Documents/Code/repos/ai-experiments/tslit-dspy-ar
git status --short
rg --files tslit_hw
sed -n '1,220p' tslit_hw/RUNBOOK_SPARK_PROTOTYPE.md
```

Step 1: verify Python package importability.

```bash
python -m compileall tslit_hw
python - <<'PY'
import tslit_hw
from tslit_hw.capture.api_surface_probe import build_surface
from tslit_hw.capture.cuda_binary_inventory import build_inventory
print("tslit_hw import OK")
PY
```

If this fails:

- Check which Python executable is active: `which python && python --version`.
- Check whether the repo root is the current directory.
- Check `pyproject.toml`; `tslit_hw` should be included in hatch packages.
- Do not install random packages unless they are necessary. The first prototype is mostly standard library.

Step 2: probe the DGX Spark API surface.

```bash
python -m tslit_hw.capture.api_surface_probe \
  --out tslit_hw/gpu_observability_api_surface.json \
  --summary-out tslit_hw/gpu_observability_api_findings.md \
  --sample-ms 100 \
  --enable-ioctl-smoke-test
```

Then inspect:

```bash
sed -n '1,220p' tslit_hw/gpu_observability_api_findings.md
python - <<'PY'
import json
from pathlib import Path
p = Path("tslit_hw/gpu_observability_api_surface.json")
d = json.loads(p.read_text())
print(json.dumps({
    "machine": d["host"]["machine"],
    "platform": d["host"]["platform"],
    "cuda_roots": d["cuda"]["cuda_roots"],
    "nvidia_smi": d["gpu"]["nvidia_smi"]["available"],
    "dcgm": d["dcgm"]["available"],
    "nsys": d["nsys"]["available"],
    "cupti_available": d["cupti"]["available"],
    "cupti_libs": d["cupti"]["libcupti_candidates"],
    "cuobjdump": d["cuda"]["cuobjdump"]["available"],
    "readelf": d["cuda"]["readelf"]["available"],
    "ioctl_strace": d["ioctl"]["available"],
    "open_questions": d["phase1_open_questions"],
}, indent=2))
PY
```

Troubleshooting API surface:

- If `nvidia-smi` is unavailable, stop and report that the NVIDIA driver/runtime is not visible from this shell. Collect:
  - `uname -a`
  - `lsb_release -a` if present
  - `ls -l /dev/nvidia*`
  - `env | rg 'CUDA|NVIDIA|LD_LIBRARY_PATH|PATH'`
- If `nvidia-smi` works but DCGM is missing, continue. DCGM is useful but not required for the first baseline.
- If `dcgmi` exists but profiling fails, record the exact error. Try read-only discovery only; do not change system services unless asked.
- If CUPTI Python bindings are missing but `libcupti.so` or `nsys` exists, continue. Direct CUPTI binding is not required for the first run.
- If `strace` is missing or not permitted, continue. IOCTL is auxiliary.
- If `cuobjdump` is missing, continue. Static binary inventory can still byte-scan markers.

Step 3: static CUDA binary inventory.

First choose scan paths that exist:

```bash
python - <<'PY'
from pathlib import Path
candidates = [
    "/usr/local/cuda",
    "/usr/local/cuda-13.0",
    "/usr/local/cuda-13.1",
    "/usr/local/cuda-13.2",
    str(Path.home() / ".cache"),
    str(Path.home() / ".triton"),
    str(Path.home() / ".ollama"),
]
for c in candidates:
    p = Path(c)
    print(("YES " if p.exists() else "NO  ") + c)
PY
```

Then run inventory on available paths. Example:

```bash
python -m tslit_hw.capture.cuda_binary_inventory \
  --path /usr/local/cuda \
  --path "$HOME/.cache" \
  --out tslit_hw/cuda_binary_inventory.json \
  --max-files 5000 \
  --byte-limit 2000000
```

If `/usr/local/cuda` or `$HOME/.cache` does not exist, omit that path or replace it with the real runtime/cache path.

Also scan the active Python environment site-packages if a local inference stack is installed there:

```bash
python - <<'PY'
import site
for p in site.getsitepackages():
    print(p)
PY
```

You can repeat the inventory command with `--path <site-packages-path>`.

Inspect the result:

```bash
python - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("tslit_hw/cuda_binary_inventory.json").read_text())
print(json.dumps(d["summary"], indent=2))
print("tools:", d["tools"])
PY
```

Troubleshooting binary inventory:

- If it finds zero CUDA-related files, that is not necessarily fatal. It may mean the model runtime JITs later or stores artifacts elsewhere.
- If scanning is slow, reduce `--max-files` or scan narrower paths.
- If files are huge and hashes are skipped, that is expected.
- If `cuobjdump` errors on many files, keep the output; the scanner is conservative and should still produce JSON.

Step 4: smoke-test the prototype without model inference.

```bash
python -m tslit_hw.prototype_runner \
  --dataset tslit_hw/data/sample_probe_dataset.jsonl \
  --out-dir tslit_hw/runs \
  --run-id dgx-dry-run-smoke \
  --dry-run \
  --repeats 1 \
  --sample-ms 100 \
  --collect-binary-inventory \
  --binary-scan-path /usr/local/cuda \
  --limit 6
```

If `/usr/local/cuda` does not exist, use an existing path or omit `--binary-scan-path`.

Inspect:

```bash
sed -n '1,220p' tslit_hw/runs/dgx-dry-run-smoke/prototype_results.md
head -n 10 tslit_hw/runs/dgx-dry-run-smoke/features.csv
python - <<'PY'
import csv
from pathlib import Path
p = Path("tslit_hw/runs/dgx-dry-run-smoke/features.csv")
rows = list(csv.DictReader(p.open()))
keys = [
    "probe_id",
    "label",
    "duration_ms",
    "nvidia_smi_samples",
    "telemetry_expected_samples",
    "telemetry_observed_samples",
    "telemetry_sample_ratio",
    "telemetry_max_sample_gap_ms",
    "telemetry_missing_metric_count",
    "telemetry_poller_failure_count",
    "telemetry_health_warning_count",
]
for row in rows:
    print({k: row.get(k) for k in keys})
PY
```

Expected dry-run behavior:

- It should create `api_surface.json`, `features.csv`, `prototype_results.md`, `run_summary.json`, and per-probe manifests.
- It should include all six sample probes unless `--limit` is changed.
- If `nvidia-smi` works, health fields should show observed monitor samples.
- If no monitor exists, health fields should show no poller and null sample ratio; that is acceptable for smoke testing.

Step 5: identify or create the local inference command.

The runner needs a command template that reads `{prompt_file}` and writes `{output_file}`.

Example template shape:

```bash
python local_infer.py --prompt-file {prompt_file} --output-file {output_file}
```

Find likely local inference scripts or runtimes:

```bash
rg -n "prompt-file|output-file|ollama|vllm|transformers|mlx|llama|generate" scripts tslit_dspy . || true
rg --files | rg "infer|generate|ollama|vllm|local"
```

If there is no adapter, ask me for the exact local model runner command. Do not guess a cloud/API model. If I provide a command that reads stdin or writes stdout, create a tiny local adapter script so the harness still gets prompt and response files.

Adapter pattern if needed:

```python
#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--prompt-file", required=True)
parser.add_argument("--output-file", required=True)
args = parser.parse_args()

prompt = Path(args.prompt_file).read_text()

# Replace this with the real local command only after confirming with the user.
proc = subprocess.run(
    ["ollama", "run", "MODEL_NAME"],
    input=prompt,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
    timeout=300,
)
Path(args.output_file).write_text(proc.stdout)
if proc.returncode != 0:
    raise SystemExit(proc.stderr)
```

Step 6: first real DGX Spark baseline.

Start conservative: one repeat, one warmup, no `nsys`, no IOCTL. Use DCGM only if present.

Replace the command template with the real local inference command:

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

If DCGM is unavailable, omit `--collect-dcgm`.

Immediately inspect monitor health before interpreting model deltas:

```bash
sed -n '1,260p' tslit_hw/runs/spark-baseline-001/prototype_results.md
python - <<'PY'
import csv
from pathlib import Path
p = Path("tslit_hw/runs/spark-baseline-001/features.csv")
rows = list(csv.DictReader(p.open()))
for r in rows:
    print(
        r["probe_id"],
        r["label"],
        "duration_ms=", r.get("duration_ms"),
        "samples=", r.get("telemetry_observed_samples"), "/", r.get("telemetry_expected_samples"),
        "ratio=", r.get("telemetry_sample_ratio"),
        "max_gap_ms=", r.get("telemetry_max_sample_gap_ms"),
        "missing_metrics=", r.get("telemetry_missing_metric_count"),
        "poller_failures=", r.get("telemetry_poller_failure_count"),
        "warnings=", r.get("telemetry_health_warning_count"),
    )
PY
```

Interpretation rules:

- If telemetry health warnings dominate, report that Phase 1 is blocked by monitor instability or missing observability support.
- If monitor health is clean but benign/adversarial rows overlap across timing/utilization/IOCTL/nsys/DCGM features, report early pressure against the thesis for this model/runtime.
- If a difference appears, do not call it detection yet. Ask whether it is repeatable and mechanically explainable.

Step 7: wider capture only after baseline works.

If baseline succeeds and monitor health looks stable, run a wider capture:

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

Only include flags supported by the machine:

- If `dcgmi` is absent, omit `--collect-dcgm`.
- If `nsys` is absent or too slow, omit `--collect-nsys`.
- If `strace` is absent or permission-denied, omit `--collect-ioctl`.

Troubleshooting wider capture:

- `nsys` can add overhead and may fail with permission or driver/toolkit mismatches. Keep the raw stderr.
- `strace` may need privileges or may not see useful NVIDIA IOCTLs depending on runtime structure. This is auxiliary.
- If the model runtime spawns worker subprocesses, `strace -ff` should capture children, but telemetry may still be incomplete if inference happens in a service outside the wrapped process. In that case, note that process ownership is not aligned with the harness and ask me whether to wrap the server process instead.
- If `nvidia-smi` samples are missing despite GPU work, check whether the command duration is too short for the sample cadence. Increase generated tokens or lower sample period if supported.
- If the first run is noisy, increase repeats and keep prompt order fixed.

Step 8: summarize results.

Prepare a concise report with:

- Exact commands run.
- API surface summary: NVIDIA driver visibility, CUDA roots, CUPTI candidates, DCGM, nsys, strace, cuobjdump/readelf.
- Static binary inventory summary: CUDA-related files, SM targets, compute targets, CUDA version hints.
- Prototype run artifact paths.
- Monitor-health summary by run and label.
- Benign vs adversarial feature summary.
- Any failures, with raw log paths.
- Whether Phase 2 is justified, blocked, or premature.

Use this decision language:

- "Proceed to Phase 2" only if the model runs locally, telemetry is healthy enough, and at least one meaningful runtime signal is visible.
- "Repeat baseline" if there are hints of separation but monitor health or variance is not stable.
- "Observability stack blocked" if monitor failures dominate.
- "Thesis under pressure" if clean repeated runs show no separation.
- "Do not start Phase 3" unless separability is repeatable and explainable.

Important artifact paths to mention:

- `tslit_hw/gpu_observability_api_surface.json`
- `tslit_hw/gpu_observability_api_findings.md`
- `tslit_hw/cuda_binary_inventory.json`
- `tslit_hw/runs/<run-id>/prototype_results.md`
- `tslit_hw/runs/<run-id>/features.csv`
- `tslit_hw/runs/<run-id>/run_summary.json`
- `tslit_hw/runs/<run-id>/probes/*/manifest.json`
- `tslit_hw/runs/<run-id>/probes/*/raw/*`

Final instruction:

Be candid. The valuable result is not "prove the thesis." The valuable result is knowing which of these is true on DGX Spark:

1. The telemetry stack is not mature enough yet.
2. The stack works, but benign/adversarial runs do not separate.
3. The stack works, and there is a repeatable, mechanically explainable hardware/runtime delta worth turning into Phase 2.
```

