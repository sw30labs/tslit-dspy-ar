"""Run a first-pass DGX Spark telemetry experiment over a small JSONL dataset.

The runner is deliberately pragmatic. It collects whatever is available on the
machine and preserves raw artifacts so the first question can be answered fast:
do adversarial and non-adversarial prompts produce different hardware traces?
"""

from __future__ import annotations

import argparse
import math
import os
import shlex
import signal
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tslit_hw.capture.api_surface_probe import build_surface
from tslit_hw.capture.cuda_binary_inventory import build_inventory as build_cuda_binary_inventory
from tslit_hw.common import (
    ensure_dir,
    parse_nvidia_smi_csv,
    parse_strace_ioctl,
    read_jsonl,
    sha256_file,
    sha256_text,
    slugify,
    utc_now,
    which,
    write_json,
)


NVIDIA_SMI_QUERY = (
    "timestamp,index,uuid,name,utilization.gpu,utilization.memory,memory.used,"
    "memory.free,power.draw,temperature.gpu,clocks.sm"
)


@dataclass
class Poller:
    name: str
    args: list[str]
    stdout_path: Path
    stderr_path: Path
    process: subprocess.Popen[str] | None = None
    start_error: str | None = None

    def start(self) -> None:
        ensure_dir(self.stdout_path.parent)
        ensure_dir(self.stderr_path.parent)
        stdout = self.stdout_path.open("w", encoding="utf-8")
        stderr = self.stderr_path.open("w", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                self.args,
                stdout=stdout,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
        finally:
            stdout.close()
            stderr.close()

    def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        if not self.process:
            return {
                "name": self.name,
                "started": False,
                "returncode": None,
                "exited_before_stop": False,
                "healthy": False,
                "start_error": self.start_error,
            }
        proc = self.process
        exited_before_stop = proc.poll() is not None
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=timeout)
        return {
            "name": self.name,
            "started": True,
            "returncode": proc.returncode,
            "exited_before_stop": exited_before_stop,
            "healthy": not exited_before_stop,
            "start_error": self.start_error,
        }


def prompt_from_record(record: dict[str, Any]) -> str:
    for key in ("prompt", "request", "input", "response_text", "baseline_response"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise ValueError(f"record has no prompt-like field: keys={sorted(record)}")


def label_from_record(record: dict[str, Any]) -> str:
    raw = record.get("label") or record.get("threat_category") or record.get("scenario_type") or "unknown"
    raw = str(raw)
    if raw == "none" or raw == "baseline":
        return "benign"
    return raw


def build_probe_id(record: dict[str, Any], repeat_index: int) -> str:
    base = str(record.get("probe_id") or record.get("example_id") or record.get("id") or uuid.uuid4())
    return f"{slugify(base)}-r{repeat_index:02d}"


def format_command(template: str, values: dict[str, str], shell: bool) -> list[str]:
    rendered = template.format(**values)
    if shell:
        return ["/bin/sh", "-lc", rendered]
    return shlex.split(rendered)


def dry_run_command(values: dict[str, str]) -> list[str]:
    script = (
        "import pathlib, time; "
        "p=pathlib.Path({prompt_file!r}).read_text(); "
        "time.sleep(0.05 + min(len(p), 5000)/100000.0); "
        "pathlib.Path({output_file!r}).write_text('dry-run response\\n' + p[:200])"
    ).format(**values)
    return [sys.executable, "-c", script]


def wrap_with_nsys(args: list[str], nsys_out_base: Path) -> list[str]:
    nsys = which("nsys")
    if not nsys:
        return args
    return [
        nsys,
        "profile",
        "--trace=cuda,nvtx,osrt",
        "--force-overwrite=true",
        "--output",
        str(nsys_out_base),
        *args,
    ]


def wrap_with_strace(args: list[str], strace_prefix: Path) -> list[str]:
    strace = which("strace")
    if not strace:
        return args
    return [
        strace,
        "-ff",
        "-ttt",
        "-T",
        "-e",
        "trace=ioctl",
        "-o",
        str(strace_prefix),
        *args,
    ]


def start_pollers(probe_dir: Path, sample_ms: int, enable_dcgm: bool) -> list[Poller]:
    pollers: list[Poller] = []
    nvidia_smi = which("nvidia-smi")
    if nvidia_smi:
        pollers.append(Poller(
            name="nvidia_smi",
            args=[
                nvidia_smi,
                f"--query-gpu={NVIDIA_SMI_QUERY}",
                "--format=csv,nounits",
                "-lms",
                str(sample_ms),
            ],
            stdout_path=probe_dir / "raw" / "nvidia_smi.csv",
            stderr_path=probe_dir / "raw" / "nvidia_smi.stderr",
        ))

    dcgmi = which("dcgmi")
    if enable_dcgm and dcgmi:
        pollers.append(Poller(
            name="dcgmi_dmon",
            args=[dcgmi, "dmon", "-d", str(max(sample_ms, 100))],
            stdout_path=probe_dir / "raw" / "dcgmi_dmon.log",
            stderr_path=probe_dir / "raw" / "dcgmi_dmon.stderr",
        ))

    for poller in pollers:
        try:
            poller.start()
        except Exception as exc:  # noqa: BLE001 - preserve prototype availability
            poller.start_error = str(exc)
            poller.stderr_path.write_text(f"failed to start {poller.name}: {exc}\n", encoding="utf-8")
            poller.process = None
    return pollers


def normalize_nvidia_smi_column(value: str) -> str:
    value = value.strip().lower()
    if "[" in value:
        value = value.split("[", 1)[0].strip()
    return value


def expected_nvidia_smi_columns() -> set[str]:
    return {normalize_nvidia_smi_column(field) for field in NVIDIA_SMI_QUERY.split(",")}


def summarize_monitor_health(
    command_result: dict[str, Any],
    nvidia_summary: dict[str, Any],
    poller_results: list[dict[str, Any]],
    sample_ms: int,
) -> dict[str, Any]:
    nvidia_poller_started = any(
        result.get("name") == "nvidia_smi" and result.get("started")
        for result in poller_results
    )
    duration_ms = command_result["duration_ms"] or 0
    expected_samples = (
        max(1, math.ceil(duration_ms / sample_ms))
        if nvidia_poller_started and sample_ms > 0 and duration_ms > 0
        else 0
    )
    observed_samples = nvidia_summary["samples"]
    observed_columns = {normalize_nvidia_smi_column(col) for col in nvidia_summary["columns"]}
    missing_columns = sorted(expected_nvidia_smi_columns() - observed_columns) if nvidia_poller_started else []
    max_gap_ms = nvidia_summary.get("timestamp", {}).get("max_gap_ms")
    sample_ratio = (observed_samples / expected_samples) if expected_samples else None
    poller_failure_count = sum(1 for result in poller_results if not result.get("healthy"))

    warning_count = poller_failure_count
    if nvidia_poller_started:
        if sample_ratio is not None and sample_ratio < 0.8:
            warning_count += 1
        if missing_columns:
            warning_count += 1
        if max_gap_ms is not None and sample_ms > 0 and max_gap_ms > sample_ms * 3:
            warning_count += 1

    return {
        "pollers": poller_results,
        "poller_count": len(poller_results),
        "poller_started_count": sum(1 for result in poller_results if result.get("started")),
        "poller_failure_count": poller_failure_count,
        "nvidia_smi_poller_started": nvidia_poller_started,
        "expected_samples": expected_samples,
        "observed_samples": observed_samples,
        "sample_ratio": sample_ratio,
        "max_sample_gap_ms": max_gap_ms,
        "missing_metric_count": len(missing_columns) if nvidia_poller_started else None,
        "missing_metrics": missing_columns,
        "warning_count": warning_count,
    }


def run_one_command(args: list[str], stdout_path: Path, stderr_path: Path, timeout: float) -> dict[str, Any]:
    ensure_dir(stdout_path.parent)
    started_wall = time.time_ns()
    started_perf = time.perf_counter()
    try:
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            proc = subprocess.run(
                args,
                stdout=stdout,
                stderr=stderr,
                text=True,
                timeout=timeout,
                check=False,
            )
        elapsed = time.perf_counter() - started_perf
        return {
            "started_ns": started_wall,
            "ended_ns": time.time_ns(),
            "duration_ms": round(elapsed * 1000, 3),
            "returncode": proc.returncode,
            "timeout": False,
        }
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - started_perf
        return {
            "started_ns": started_wall,
            "ended_ns": time.time_ns(),
            "duration_ms": round(elapsed * 1000, 3),
            "returncode": None,
            "timeout": True,
        }


def summarize_probe(
    probe_dir: Path,
    command_result: dict[str, Any],
    generated_tokens: int | None,
    poller_results: list[dict[str, Any]],
    sample_ms: int,
) -> dict[str, Any]:
    nvidia_summary = parse_nvidia_smi_csv(probe_dir / "raw" / "nvidia_smi.csv")
    strace_files = sorted((probe_dir / "raw" / "ioctl").glob("strace*")) if (probe_dir / "raw" / "ioctl").exists() else []
    ioctl_summary = parse_strace_ioctl(strace_files)
    nsys_reports = sorted(str(p.name) for p in (probe_dir / "raw" / "nsys").glob("*")) if (probe_dir / "raw" / "nsys").exists() else []
    monitor_health = summarize_monitor_health(command_result, nvidia_summary, poller_results, sample_ms)

    duration_s = command_result["duration_ms"] / 1000 if command_result["duration_ms"] else 0
    features = {
        "duration_ms": command_result["duration_ms"],
        "returncode": command_result["returncode"],
        "timeout": command_result["timeout"],
        "generated_tokens": generated_tokens,
        "nvidia_smi_samples": nvidia_summary["samples"],
        "telemetry_poller_count": monitor_health["poller_count"],
        "telemetry_poller_started_count": monitor_health["poller_started_count"],
        "telemetry_poller_failure_count": monitor_health["poller_failure_count"],
        "telemetry_expected_samples": monitor_health["expected_samples"],
        "telemetry_observed_samples": monitor_health["observed_samples"],
        "telemetry_sample_ratio": monitor_health["sample_ratio"],
        "telemetry_max_sample_gap_ms": monitor_health["max_sample_gap_ms"],
        "telemetry_missing_metric_count": monitor_health["missing_metric_count"],
        "telemetry_health_warning_count": monitor_health["warning_count"],
        "ioctl_call_rate": (ioctl_summary["records"] / duration_s) if duration_s and ioctl_summary["records"] else None,
        "ioctl_unique_cmd_count": ioctl_summary["unique_cmd_count"],
        "ioctl_error_rate": ioctl_summary["error_rate"],
        "nsys_artifact_count": len(nsys_reports),
    }

    for column, stats in nvidia_summary["numeric"].items():
        safe = slugify(column.lower().replace(" ", "_").replace(".", "_"), fallback="metric")
        features[f"nvidia_smi_{safe}_mean"] = stats["mean"]
        features[f"nvidia_smi_{safe}_max"] = stats["max"]

    return {
        "features": features,
        "nvidia_smi": nvidia_summary,
        "monitor_health": monitor_health,
        "ioctl": ioctl_summary,
        "nsys_reports": nsys_reports,
    }


def run_probe(
    record: dict[str, Any],
    repeat_index: int,
    args: argparse.Namespace,
    run_dir: Path,
) -> dict[str, Any]:
    probe_id = build_probe_id(record, repeat_index)
    label = label_from_record(record)
    prompt = prompt_from_record(record)
    prompt_hash = sha256_text(prompt)
    probe_dir = ensure_dir(run_dir / "probes" / probe_id)
    raw_dir = ensure_dir(probe_dir / "raw")
    ensure_dir(raw_dir / "ioctl")
    ensure_dir(raw_dir / "nsys")

    prompt_file = probe_dir / "prompt.txt"
    response_file = probe_dir / "response.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    values = {
        "prompt_file": str(prompt_file),
        "output_file": str(response_file),
        "probe_id": probe_id,
        "label": label,
        "probe_dir": str(probe_dir),
    }

    if args.dry_run:
        base_cmd = dry_run_command(values)
    elif args.command_template:
        base_cmd = format_command(args.command_template, values, args.shell)
    else:
        raise ValueError("--command-template is required unless --dry-run is set")

    warmup_cmd = base_cmd
    for _ in range(args.warmups):
        run_one_command(warmup_cmd, raw_dir / "warmup.stdout", raw_dir / "warmup.stderr", args.timeout)

    measured_cmd = list(base_cmd)
    if args.collect_nsys and which("nsys"):
        measured_cmd = wrap_with_nsys(measured_cmd, raw_dir / "nsys" / "trace")
    if args.collect_ioctl and which("strace"):
        measured_cmd = wrap_with_strace(measured_cmd, raw_dir / "ioctl" / "strace")

    pollers = start_pollers(probe_dir, args.sample_ms, args.collect_dcgm)
    try:
        command_result = run_one_command(
            measured_cmd,
            raw_dir / "model.stdout",
            raw_dir / "model.stderr",
            args.timeout,
        )
    finally:
        poller_results = [poller.stop() for poller in pollers]

    generated_tokens = None
    if response_file.exists():
        generated_tokens = len(response_file.read_text(errors="ignore").split())

    summary = summarize_probe(probe_dir, command_result, generated_tokens, poller_results, args.sample_ms)
    manifest = {
        "schema_version": "tslit_hw.prototype_probe.v1",
        "probe_id": probe_id,
        "run_id": run_dir.name,
        "created_at": utc_now(),
        "label": label,
        "scenario": record.get("scenario") or record.get("scenario_type") or "unknown",
        "affiliation": record.get("affiliation"),
        "virtual_time": record.get("virtual_time") or record.get("probe_date"),
        "prompt_sha256": prompt_hash,
        "input_record": record,
        "command": {
            "base": base_cmd,
            "measured": measured_cmd,
            "dry_run": args.dry_run,
            "shell": args.shell,
        },
        "timing": command_result,
        "pollers": poller_results,
        "artifacts": {
            "probe_dir": str(probe_dir),
            "prompt": str(prompt_file),
            "response": str(response_file),
            "nvidia_smi_csv": str(raw_dir / "nvidia_smi.csv"),
            "dcgmi_dmon_log": str(raw_dir / "dcgmi_dmon.log"),
            "ioctl_dir": str(raw_dir / "ioctl"),
            "nsys_dir": str(raw_dir / "nsys"),
        },
        "artifact_hashes": {
            "prompt": sha256_file(prompt_file),
            "response": sha256_file(response_file),
            "nvidia_smi_csv": sha256_file(raw_dir / "nvidia_smi.csv"),
        },
        "summary": summary,
    }
    write_json(probe_dir / "manifest.json", manifest)
    write_json(probe_dir / "features.json", {
        "schema_version": "tslit_hw.prototype_features.v1",
        "probe_id": probe_id,
        "label": label,
        **summary,
    })
    return manifest


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv

    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def group_summary(rows: list[dict[str, Any]], group_key: str = "label") -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(group_key)), []).append(row)
    out: dict[str, Any] = {}
    for label, label_rows in grouped.items():
        metrics: dict[str, Any] = {"count": len(label_rows)}
        numeric_keys = {
            key
            for row in label_rows
            for key, value in row.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        for key in sorted(numeric_keys):
            values = [float(row[key]) for row in label_rows if isinstance(row.get(key), (int, float))]
            if values:
                metrics[key] = {
                    "mean": statistics.mean(values),
                    "min": min(values),
                    "max": max(values),
                }
                if len(values) > 1:
                    metrics[key]["stdev"] = statistics.stdev(values)
        out[label] = metrics
    return out


def write_markdown_summary(path: Path, run_id: str, rows: list[dict[str, Any]], surface: dict[str, Any]) -> None:
    binary_grouped = group_summary(rows, "binary_label")
    detailed_grouped = group_summary(rows, "label")
    inventory = surface.get("cuda_binary_inventory")
    inventory_summary = inventory.get("summary") if isinstance(inventory, dict) else None
    lines = [
        f"# TSLIT-HW Prototype Run: `{run_id}`",
        "",
        "## Capability Snapshot",
        "",
        f"- `nvidia-smi`: {surface['gpu']['nvidia_smi']['available']}",
        f"- `dcgmi`: {surface['dcgm']['available']}",
        f"- `nsys`: {surface['nsys']['available']}",
        f"- `strace` IOCTL: {surface['ioctl']['available']}",
        f"- CUPTI candidates: {len(surface['cupti']['libcupti_candidates'])}",
        f"- CUDA binary inventory: {bool(inventory_summary)}",
        "",
    ]
    if inventory_summary:
        lines.extend([
            "## Static CUDA Binary Inventory",
            "",
            f"- Candidate files: {inventory_summary['candidate_files']}",
            f"- CUDA-related files: {inventory_summary['cuda_related_files']}",
            f"- SM targets: `{', '.join(inventory_summary['sm_arches']) or 'none detected'}`",
            f"- Compute targets: `{', '.join(inventory_summary['compute_arches']) or 'none detected'}`",
            f"- CUDA versions: `{', '.join(inventory_summary['cuda_versions']) or 'none detected'}`",
            "",
        ])
    lines.extend([
        "## Benign vs Adversarial Summary",
        "",
    ])
    for label, metrics in binary_grouped.items():
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"- Probes: {metrics['count']}")
        for key in (
            "duration_ms",
            "telemetry_sample_ratio",
            "telemetry_health_warning_count",
            "telemetry_poller_failure_count",
            "telemetry_missing_metric_count",
            "telemetry_max_sample_gap_ms",
            "ioctl_call_rate",
            "ioctl_unique_cmd_count",
            "ioctl_error_rate",
        ):
            if key in metrics:
                val = metrics[key]
                lines.append(f"- `{key}` mean: {val['mean']:.4f} (min {val['min']:.4f}, max {val['max']:.4f})")
        lines.append("")
    lines.append("## Detailed Label Summary")
    lines.append("")
    for label, metrics in detailed_grouped.items():
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"- Probes: {metrics['count']}")
        if "duration_ms" in metrics:
            val = metrics["duration_ms"]
            lines.append(f"- `duration_ms` mean: {val['mean']:.4f} (min {val['min']:.4f}, max {val['max']:.4f})")
        lines.append("")
    lines.extend([
        "## Interpretation",
        "",
        "Phase 1 asks two questions: what did the GPU do, and did our monitors stay healthy while it did it?",
        "",
        "This report is intentionally descriptive. Look first for large, repeatable differences between benign and adversarial label groups, but interpret them only after checking telemetry health. If the monitors drop samples, lose fields, or exit early, that is a Phase 1 result about observability maturity, not yet an adversarial-model signal.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="JSONL file with prompt/request/input and label/threat_category fields")
    parser.add_argument("--out-dir", default="tslit_hw/runs")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--command-template", help="Command template; placeholders: {prompt_file}, {output_file}, {probe_id}, {label}, {probe_dir}")
    parser.add_argument("--shell", action="store_true", help="Render command-template through /bin/sh -lc")
    parser.add_argument("--dry-run", action="store_true", help="Use a tiny local Python command instead of model inference")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--warmups", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--sample-ms", type=int, default=100)
    parser.add_argument("--collect-dcgm", action="store_true")
    parser.add_argument("--collect-ioctl", action="store_true")
    parser.add_argument("--collect-nsys", action="store_true")
    parser.add_argument("--collect-binary-inventory", action="store_true")
    parser.add_argument("--binary-scan-path", action="append", default=[], help="File or directory for static CUDA binary inventory. Repeatable.")
    parser.add_argument("--binary-scan-max-files", type=int, default=2500)
    parser.add_argument("--binary-scan-byte-limit", type=int, default=2_000_000)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.dry_run and not args.command_template:
        raise SystemExit("--command-template is required unless --dry-run is set")

    run_id = args.run_id or f"spark-prototype-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = ensure_dir(Path(args.out_dir) / slugify(run_id))
    records = read_jsonl(Path(args.dataset))
    if args.limit is not None:
        records = records[: args.limit]

    surface = build_surface(argparse.Namespace(
        out=str(run_dir / "api_surface.json"),
        sample_ms=args.sample_ms,
        enable_ioctl_smoke_test=args.collect_ioctl,
        smoke_command=None,
        smoke_timeout=30.0,
    ))
    if args.collect_binary_inventory:
        scan_paths = args.binary_scan_path or [str(Path.cwd())]
        inventory_args = argparse.Namespace(
            path=scan_paths,
            max_files=args.binary_scan_max_files,
            byte_limit=args.binary_scan_byte_limit,
            hash_limit_bytes=100_000_000,
            tool_timeout=10.0,
            include_all=False,
            use_readelf=True,
            use_cuobjdump=True,
        )
        inventory = build_cuda_binary_inventory(inventory_args)
        inventory_path = run_dir / "cuda_binary_inventory.json"
        write_json(inventory_path, inventory)
        surface["cuda_binary_inventory"] = {
            "path": str(inventory_path),
            "summary": inventory["summary"],
        }
    write_json(run_dir / "api_surface.json", surface)

    manifests: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    for repeat_index in range(args.repeats):
        for record in records:
            manifest = run_probe(record, repeat_index, args, run_dir)
            manifests.append(manifest)
            row = {
                "probe_id": manifest["probe_id"],
                "label": manifest["label"],
                "binary_label": "benign" if manifest["label"] == "benign" else "adversarial",
                "scenario": manifest["scenario"],
                **manifest["summary"]["features"],
            }
            feature_rows.append(row)
            print(f"{manifest['probe_id']}: label={manifest['label']} duration={row['duration_ms']}ms")

    write_json(run_dir / "run_summary.json", {
        "schema_version": "tslit_hw.prototype_run.v1",
        "run_id": run_id,
        "created_at": utc_now(),
        "dataset": str(Path(args.dataset).resolve()),
        "args": vars(args),
        "cuda_binary_inventory": surface.get("cuda_binary_inventory"),
        "probe_count": len(manifests),
        "binary_group_summary": group_summary(feature_rows, "binary_label"),
        "group_summary": group_summary(feature_rows, "label"),
        "probes": [
            {
                "probe_id": m["probe_id"],
                "label": m["label"],
                "manifest": m["artifacts"]["probe_dir"] + "/manifest.json",
            }
            for m in manifests
        ],
    })
    write_csv(run_dir / "features.csv", feature_rows)
    write_markdown_summary(run_dir / "prototype_results.md", run_id, feature_rows, surface)
    print(f"Wrote run artifacts under {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
