"""Probe DGX Spark hardware-observability API availability.

This module intentionally avoids importing NVIDIA libraries at module import
time. DGX Spark / CUDA 13.x support is new enough that the first prototype
needs to report what exists instead of assuming any single stack works.
"""

from __future__ import annotations

import argparse
import ctypes.util
import glob
import importlib.util
import os
from pathlib import Path
from typing import Any

from tslit_hw.common import command_probe, host_info, run_command, utc_now, which, write_json


CUDA_CANDIDATES = [
    "/usr/local/cuda",
    "/usr/local/cuda-13.0",
    "/usr/local/cuda-13.1",
    "/usr/local/cuda-13.2",
]

CUPTI_LIB_PATTERNS = [
    "/usr/local/cuda*/extras/CUPTI/lib64/libcupti.so*",
    "/usr/local/cuda*/targets/*/lib/libcupti.so*",
    "/usr/lib/*/libcupti.so*",
    "/usr/lib64/libcupti.so*",
]

PYTHON_CUPTI_MODULES = [
    "cupti",
    "cupti_python",
    "nvidia.cuda_cupti",
    "nvidia_cuda_cupti",
]

DCGM_FIELD_NAMES = [
    "DCGM_FI_DEV_GPU_UTIL",
    "DCGM_FI_DEV_MEM_COPY_UTIL",
    "DCGM_FI_PROF_SM_ACTIVE",
    "DCGM_FI_PROF_SM_OCCUPANCY",
    "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE",
    "DCGM_FI_PROF_PIPE_FP32_ACTIVE",
    "DCGM_FI_PROF_PIPE_FP16_ACTIVE",
    "DCGM_FI_PROF_DRAM_ACTIVE",
    "DCGM_FI_PROF_PCIE_TX_BYTES",
    "DCGM_FI_PROF_PCIE_RX_BYTES",
    "DCGM_FI_PROF_NVLINK_TX_BYTES",
    "DCGM_FI_PROF_NVLINK_RX_BYTES",
    "DCGM_FI_DEV_POWER_USAGE",
    "DCGM_FI_DEV_GPU_TEMP",
    "DCGM_FI_DEV_SM_CLOCK",
]


def find_cuda_roots() -> list[str]:
    roots: list[str] = []
    for candidate in CUDA_CANDIDATES:
        if Path(candidate).exists():
            roots.append(candidate)
    cuda_home = os.environ.get("CUDA_HOME") or os.environ.get("CUDA_PATH")
    if cuda_home and Path(cuda_home).exists() and cuda_home not in roots:
        roots.insert(0, cuda_home)
    return roots


def find_cupti_libs() -> list[str]:
    libs: set[str] = set()
    found = ctypes.util.find_library("cupti")
    if found:
        libs.add(found)
    for pattern in CUPTI_LIB_PATTERNS:
        libs.update(glob.glob(pattern))
    return sorted(libs)


def python_module_status(names: list[str]) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for name in names:
        try:
            available = importlib.util.find_spec(name) is not None
            error = None
        except ModuleNotFoundError as exc:
            available = False
            error = str(exc)
        status[name] = {
            "available": available,
            "error": error,
        }
    return status


def run_optional_smoke_command(command: str | None, timeout: float) -> dict[str, Any] | None:
    if not command:
        return None
    return run_command(["/bin/sh", "-lc", command], timeout=timeout)


def build_surface(args: argparse.Namespace) -> dict[str, Any]:
    nvidia_smi = command_probe("nvidia-smi", ["--query-gpu=index,uuid,name,driver_version", "--format=csv,noheader"], timeout=8)
    dcgmi = command_probe("dcgmi", ["--version"], timeout=8)
    nsys = command_probe("nsys", ["--version"], timeout=8)
    nvcc = command_probe("nvcc", ["--version"], timeout=8)
    cuobjdump = command_probe("cuobjdump", ["--version"], timeout=8)
    readelf = command_probe("readelf", ["--version"], timeout=8)
    strace = command_probe("strace", ["-V"], timeout=8)

    cupti_libs = find_cupti_libs()
    cuda_roots = find_cuda_roots()
    py_cupti = python_module_status(PYTHON_CUPTI_MODULES)

    dcgm_discovery = None
    if dcgmi["available"]:
        dcgm_discovery = run_command([dcgmi["path"], "discovery", "-l"], timeout=8)

    ioctl_smoke = None
    if args.enable_ioctl_smoke_test and strace["available"]:
        ioctl_out = Path(args.out).with_suffix(".ioctl_smoke.strace")
        cmd = [
            strace["path"],
            "-f",
            "-e",
            "trace=ioctl",
            "-o",
            str(ioctl_out),
            "true",
        ]
        smoke = run_command(cmd, timeout=8)
        ioctl_smoke = {
            "command": cmd,
            "ok": smoke["ok"],
            "returncode": smoke["returncode"],
            "trace_path": str(ioctl_out),
            "trace_bytes": ioctl_out.stat().st_size if ioctl_out.exists() else 0,
            "stderr": smoke["stderr"][-2000:],
        }

    surface = {
        "schema_version": "tslit_hw.api_surface.v1",
        "generated_at": utc_now(),
        "host": host_info(),
        "cuda": {
            "cuda_roots": cuda_roots,
            "nvcc": nvcc,
            "cuobjdump": cuobjdump,
            "readelf": readelf,
        },
        "gpu": {
            "nvidia_smi": nvidia_smi,
        },
        "cupti": {
            "available": bool(cupti_libs) or any(v["available"] for v in py_cupti.values()),
            "python_modules": py_cupti,
            "libcupti_candidates": cupti_libs,
            "activity_kinds_planned": [
                "CONCURRENT_KERNEL",
                "MEMCPY",
                "MEMSET",
                "SYNCHRONIZATION",
                "UNIFIED_MEMORY_COUNTER",
                "OVERHEAD",
                "MARKER",
                "MARKER_DATA",
            ],
            "note": "Prototype uses nsys/CUPTI-derived traces if available; direct CUPTI binding is probed but not required for the first run.",
        },
        "dcgm": {
            "available": dcgmi["available"],
            "dcgmi": dcgmi,
            "discovery": dcgm_discovery,
            "fields_requested": DCGM_FIELD_NAMES,
            "sample_period_ms": args.sample_ms,
        },
        "nsys": {
            "available": nsys["available"],
            "probe": nsys,
        },
        "ioctl": {
            "available": strace["available"],
            "tracer": "strace" if strace["available"] else "none",
            "scope": "local_process_only",
            "payload_decoding": False,
            "strace": strace,
            "smoke_test": ioctl_smoke,
        },
        "smoke_command": run_optional_smoke_command(args.smoke_command, args.smoke_timeout),
        "phase1_open_questions": {
            "cupti_python_aarch64_cuda13": "pass" if any(v["available"] for v in py_cupti.values()) else "unknown",
            "libcupti_cuda13": "pass" if cupti_libs else "unknown",
            "dcgm_permissions_dgx_spark": "pass" if dcgm_discovery and dcgm_discovery.get("ok") else "unknown",
            "ioctl_local_process_trace": "pass" if ioctl_smoke and ioctl_smoke["ok"] else ("unknown" if args.enable_ioctl_smoke_test else "not_tested"),
            "warm_forward_overhead": "unknown",
            "unique_kernel_count_budget": "unknown",
        },
    }
    return surface


def write_summary(path: Path, surface: dict[str, Any]) -> None:
    cupti_state = "available" if surface["cupti"]["available"] else "not confirmed"
    dcgm_state = "available" if surface["dcgm"]["available"] else "not found"
    nsys_state = "available" if surface["nsys"]["available"] else "not found"
    ioctl_state = "available" if surface["ioctl"]["available"] else "not found"
    cuobjdump_state = "available" if surface["cuda"]["cuobjdump"]["available"] else "not found"
    readelf_state = "available" if surface["cuda"]["readelf"]["available"] else "not found"
    lines = [
        "# GPU Observability API Findings",
        "",
        f"Generated: `{surface['generated_at']}`",
        "",
        "## Environment",
        "",
        f"- Platform: `{surface['host']['platform']}`",
        f"- Machine: `{surface['host']['machine']}`",
        f"- Python: `{surface['host']['python']}`",
        f"- CUDA roots: `{', '.join(surface['cuda']['cuda_roots']) or 'none detected'}`",
        "",
        "## Capture Surfaces",
        "",
        f"- CUPTI: **{cupti_state}**. `libcupti` candidates: {len(surface['cupti']['libcupti_candidates'])}.",
        f"- DCGM: **{dcgm_state}**.",
        f"- Nsight Systems (`nsys`): **{nsys_state}**.",
        f"- IOCTL tracing via `strace`: **{ioctl_state}**.",
        f"- CUDA binary inspection (`cuobjdump` / `readelf`): **{cuobjdump_state}** / **{readelf_state}**.",
        "",
        "## Recommendation",
        "",
    ]

    if surface["gpu"]["nvidia_smi"]["available"] and (surface["dcgm"]["available"] or surface["nsys"]["available"]):
        lines.append("Green/yellow: proceed with the prototype runner, but treat missing capture surfaces as null auxiliary channels.")
    elif surface["gpu"]["nvidia_smi"]["available"]:
        lines.append("Yellow: GPU is visible, but DCGM/nsys were not confirmed. Start with `nvidia-smi` polling and command timing.")
    else:
        lines.append("Red: `nvidia-smi` was not found or did not run; fix the NVIDIA stack before running probe collection.")

    lines.extend([
        "",
        "IOCTL data remains auxiliary. It should corroborate CUPTI/DCGM or runtime anomalies, not become the first detector rule.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="tslit_hw/gpu_observability_api_surface.json")
    parser.add_argument("--summary-out", default="tslit_hw/gpu_observability_api_findings.md")
    parser.add_argument("--sample-ms", type=int, default=100)
    parser.add_argument("--enable-ioctl-smoke-test", action="store_true")
    parser.add_argument("--smoke-command", help="Optional shell command to run as a workload smoke test")
    parser.add_argument("--smoke-timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    surface = build_surface(args)
    out = Path(args.out)
    write_json(out, surface)
    write_summary(Path(args.summary_out), surface)
    print(f"Wrote {out}")
    print(f"Wrote {args.summary_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
