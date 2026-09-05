"""Static CUDA binary inventory for TSLIT-HW.

This scanner looks for CUDA fatbin/cubin/PTX indicators in local binaries and
libraries. It is intentionally conservative: it records fingerprints and tool
outputs, but it does not try to reverse engineer private NVIDIA formats.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from tslit_hw.common import ensure_dir, run_command, sha256_file, utc_now, which, write_json


DEFAULT_SUFFIXES = {
    "",
    ".a",
    ".o",
    ".so",
    ".bin",
    ".cubin",
    ".fatbin",
    ".ptx",
    ".pyd",
}

CUDA_SECTION_MARKERS = [
    ".nv_fatbin",
    ".nvFatBinSegment",
    "__nv_relfatbin",
    "__cudaFatCubin",
]

ARCH_RE = re.compile(r"\b(?:sm|compute)_?([0-9]{2,3})\b", re.IGNORECASE)
CUDA_VERSION_RE = re.compile(r"\bCUDA(?: Toolkit)?\s*([0-9]+(?:\.[0-9]+){0,2})\b", re.IGNORECASE)


def is_candidate_path(path: Path, executable: bool) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    return suffix in DEFAULT_SUFFIXES or ".so" in name or executable


def iter_candidate_files(paths: Iterable[Path], max_files: int) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            files = [root]
        else:
            files = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in {".git", "__pycache__", ".venv", "node_modules"}
                ]
                for filename in filenames:
                    files.append(Path(dirpath) / filename)
                    if len(files) >= max_files:
                        break
                if len(files) >= max_files:
                    break

        for path in files:
            if len(candidates) >= max_files:
                return candidates
            try:
                resolved = path.resolve()
                stat = resolved.stat()
            except OSError:
                continue
            if resolved in seen:
                continue
            executable = bool(stat.st_mode & 0o111)
            if is_candidate_path(resolved, executable):
                seen.add(resolved)
                candidates.append(resolved)
    return candidates


def read_head(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as f:
            return f.read(limit)
    except OSError:
        return b""


def safe_text(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def scan_strings(path: Path, byte_limit: int) -> dict[str, Any]:
    data = read_head(path, byte_limit)
    text = safe_text(data)
    markers = [marker for marker in CUDA_SECTION_MARKERS if marker in text]
    arches = sorted({f"sm_{match.group(1)}" for match in ARCH_RE.finditer(text) if match.group(0).lower().startswith("sm")})
    compute_arches = sorted({f"compute_{match.group(1)}" for match in ARCH_RE.finditer(text) if match.group(0).lower().startswith("compute")})
    versions = sorted({match.group(1) for match in CUDA_VERSION_RE.finditer(text)})
    magic = {
        "elf": data.startswith(b"\x7fELF"),
        "fatbin_header": b"\x50\xED\x55\xBA" in data or b"\xBA\x55\xED\x50" in data,
        "fatbin_wrapper_hint": b"FbC" in data,
    }
    return {
        "byte_sampled": len(data),
        "markers": markers,
        "sm_arches": arches,
        "compute_arches": compute_arches,
        "cuda_versions": versions,
        "magic": magic,
        "looks_cuda_related": bool(markers or arches or compute_arches or versions or any(magic.values())),
    }


def run_readelf(path: Path, timeout: float) -> dict[str, Any] | None:
    readelf = which("readelf")
    if not readelf:
        return None
    result = run_command([readelf, "-S", str(path)], timeout=timeout)
    stdout = result.get("stdout") or ""
    sections = [marker for marker in CUDA_SECTION_MARKERS if marker in stdout]
    return {
        "available": True,
        "ok": result["ok"],
        "returncode": result["returncode"],
        "elapsed_ms": result["elapsed_ms"],
        "cuda_sections": sections,
        "stderr_tail": (result.get("stderr") or "")[-1000:],
    }


def run_cuobjdump(path: Path, timeout: float) -> dict[str, Any] | None:
    cuobjdump = which("cuobjdump")
    if not cuobjdump:
        return None
    result = run_command([cuobjdump, "--list-elf", str(path)], timeout=timeout)
    stdout = result.get("stdout") or ""
    stderr = result.get("stderr") or ""
    text = stdout + "\n" + stderr
    arches = sorted({f"sm_{match.group(1)}" for match in ARCH_RE.finditer(text) if match.group(0).lower().startswith("sm")})
    compute_arches = sorted({f"compute_{match.group(1)}" for match in ARCH_RE.finditer(text) if match.group(0).lower().startswith("compute")})
    return {
        "available": True,
        "ok": result["ok"],
        "returncode": result["returncode"],
        "elapsed_ms": result["elapsed_ms"],
        "sm_arches": arches,
        "compute_arches": compute_arches,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
    }


def should_run_readelf(path: Path, string_scan: dict[str, Any]) -> bool:
    suffix = path.suffix.lower()
    return bool(string_scan["magic"]["elf"] or suffix in {".o", ".a", ".so"} or ".so" in path.name.lower())


def should_run_cuobjdump(path: Path, string_scan: dict[str, Any], readelf: dict[str, Any] | None) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".cubin", ".fatbin", ".ptx"}:
        return True
    if string_scan["markers"] or string_scan["sm_arches"] or string_scan["compute_arches"]:
        return True
    if readelf and readelf.get("cuda_sections"):
        return True
    return False


def inspect_file(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    stat = path.stat()
    string_scan = scan_strings(path, args.byte_limit)
    readelf = (
        run_readelf(path, args.tool_timeout)
        if args.use_readelf and should_run_readelf(path, string_scan)
        else None
    )
    cuobjdump = (
        run_cuobjdump(path, args.tool_timeout)
        if args.use_cuobjdump and should_run_cuobjdump(path, string_scan, readelf)
        else None
    )
    cuda_related = string_scan["looks_cuda_related"]
    if readelf and readelf.get("cuda_sections"):
        cuda_related = True
    if cuobjdump and cuobjdump.get("sm_arches"):
        cuda_related = True
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mode": oct(stat.st_mode & 0o777),
        "sha256": sha256_file(path) if stat.st_size <= args.hash_limit_bytes else None,
        "hash_skipped": stat.st_size > args.hash_limit_bytes,
        "string_scan": string_scan,
        "readelf": readelf,
        "cuobjdump": cuobjdump,
        "cuda_related": cuda_related,
    }


def build_inventory(args: argparse.Namespace) -> dict[str, Any]:
    roots = [Path(p).expanduser() for p in args.path]
    candidates = iter_candidate_files(roots, args.max_files)
    inspected = [inspect_file(path, args) for path in candidates]
    cuda_related = [item for item in inspected if item["cuda_related"]]
    sm_arches = sorted({
        arch
        for item in cuda_related
        for arch in item["string_scan"].get("sm_arches", [])
    } | {
        arch
        for item in cuda_related
        if item.get("cuobjdump")
        for arch in item["cuobjdump"].get("sm_arches", [])
    })
    compute_arches = sorted({
        arch
        for item in cuda_related
        for arch in item["string_scan"].get("compute_arches", [])
    } | {
        arch
        for item in cuda_related
        if item.get("cuobjdump")
        for arch in item["cuobjdump"].get("compute_arches", [])
    })
    cuda_versions = sorted({
        version
        for item in cuda_related
        for version in item["string_scan"].get("cuda_versions", [])
    })
    return {
        "schema_version": "tslit_hw.cuda_binary_inventory.v1",
        "generated_at": utc_now(),
        "roots": [str(root) for root in roots],
        "tools": {
            "readelf": which("readelf"),
            "cuobjdump": which("cuobjdump"),
        },
        "limits": {
            "max_files": args.max_files,
            "byte_limit": args.byte_limit,
            "hash_limit_bytes": args.hash_limit_bytes,
        },
        "summary": {
            "candidate_files": len(candidates),
            "inspected_files": len(inspected),
            "cuda_related_files": len(cuda_related),
            "sm_arches": sm_arches,
            "compute_arches": compute_arches,
            "cuda_versions": cuda_versions,
        },
        "files": inspected if args.include_all else cuda_related,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", action="append", required=True, help="File or directory to scan. Repeatable.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-files", type=int, default=2500)
    parser.add_argument("--byte-limit", type=int, default=2_000_000)
    parser.add_argument("--hash-limit-bytes", type=int, default=100_000_000)
    parser.add_argument("--tool-timeout", type=float, default=10.0)
    parser.add_argument("--include-all", action="store_true", help="Include non-CUDA-looking files in output.")
    parser.add_argument("--no-readelf", dest="use_readelf", action="store_false")
    parser.add_argument("--no-cuobjdump", dest="use_cuobjdump", action="store_false")
    parser.set_defaults(use_readelf=True, use_cuobjdump=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inventory = build_inventory(args)
    out = Path(args.out)
    ensure_dir(out.parent)
    write_json(out, inventory)
    print(json.dumps(inventory["summary"], indent=2, sort_keys=True))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
