"""Shared helpers for the TSLIT-HW prototype."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
    return records


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(value: str, fallback: str = "probe") -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return value[:96] or fallback


def which(name: str) -> str | None:
    return shutil.which(name)


def run_command(
    args: list[str],
    timeout: float = 10.0,
    cwd: Path | None = None,
) -> dict[str, Any]:
    def coerce_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        elapsed = time.perf_counter() - started
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "elapsed_ms": round(elapsed * 1000, 3),
            "error": None,
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": coerce_text(exc.stdout),
            "stderr": coerce_text(exc.stderr),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": f"timeout after {timeout}s",
        }


def command_probe(name: str, args: list[str], timeout: float = 5.0) -> dict[str, Any]:
    path = which(name)
    result = {
        "name": name,
        "path": path,
        "available": bool(path),
        "probe": None,
    }
    if path:
        result["probe"] = run_command([path, *args], timeout=timeout)
    return result


def host_info() -> dict[str, Any]:
    os_release: dict[str, str] = {}
    os_release_path = Path("/etc/os-release")
    if os_release_path.exists():
        for raw_line in os_release_path.read_text(errors="ignore").splitlines():
            if "=" not in raw_line:
                continue
            key, val = raw_line.split("=", 1)
            os_release[key] = val.strip().strip('"')

    return {
        "generated_at": utc_now(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "kernel": platform.release(),
        "os_release": os_release,
        "cwd": os.getcwd(),
    }


def parse_nvidia_smi_csv(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {"samples": 0, "columns": [], "numeric": {}, "timestamp": {"parsed": 0, "max_gap_ms": None}}

    lines = [line for line in path.read_text(errors="ignore").splitlines() if line.strip()]
    if not lines:
        return {"samples": 0, "columns": [], "numeric": {}, "timestamp": {"parsed": 0, "max_gap_ms": None}}

    reader = csv.DictReader(lines)
    rows = list(reader)
    numeric: dict[str, dict[str, float]] = {}
    for field in reader.fieldnames or []:
        values: list[float] = []
        for row in rows:
            raw = (row.get(field) or "").strip()
            if raw == "[Not Supported]":
                continue
            try:
                values.append(float(raw))
            except ValueError:
                continue
        if values:
            numeric[field] = {
                "min": min(values),
                "max": max(values),
                "mean": sum(values) / len(values),
            }

    timestamps: list[datetime] = []
    timestamp_field = next((field for field in reader.fieldnames or [] if field.lower().strip() == "timestamp"), None)
    if timestamp_field:
        for row in rows:
            raw = (row.get(timestamp_field) or "").strip()
            if not raw:
                continue
            parsed = None
            for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            if parsed is None:
                try:
                    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    parsed = None
            if parsed is not None:
                timestamps.append(parsed)

    timestamps.sort()
    gaps_ms = [
        (right - left).total_seconds() * 1000
        for left, right in zip(timestamps, timestamps[1:])
    ]
    return {
        "samples": len(rows),
        "columns": reader.fieldnames or [],
        "numeric": numeric,
        "timestamp": {
            "parsed": len(timestamps),
            "max_gap_ms": max(gaps_ms) if gaps_ms else None,
        },
    }


IOCTL_RE = re.compile(
    r"ioctl\((?P<fd>\d+),\s*(?P<cmd>[^,\)]+)(?:,\s*(?P<arg>.*?))?\)\s*=\s*"
    r"(?P<ret>-?\d+)(?:\s+(?P<errno>[A-Z0-9_]+))?"
)


def parse_strace_ioctl(paths: Iterable[Path]) -> dict[str, Any]:
    records = 0
    unique_cmds: set[str] = set()
    errors = 0
    raw_lines = 0
    by_cmd: dict[str, int] = {}
    by_errno: dict[str, int] = {}

    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        for line in path.read_text(errors="ignore").splitlines():
            if "ioctl(" not in line:
                continue
            raw_lines += 1
            match = IOCTL_RE.search(line)
            if not match:
                continue
            records += 1
            cmd = match.group("cmd").strip()
            unique_cmds.add(cmd)
            by_cmd[cmd] = by_cmd.get(cmd, 0) + 1
            errno = match.group("errno")
            ret = int(match.group("ret"))
            if errno or ret < 0:
                errors += 1
            if errno:
                by_errno[errno] = by_errno.get(errno, 0) + 1

    return {
        "raw_ioctl_lines": raw_lines,
        "records": records,
        "unique_cmd_count": len(unique_cmds),
        "error_count": errors,
        "error_rate": (errors / records) if records else None,
        "top_cmds": sorted(by_cmd.items(), key=lambda item: item[1], reverse=True)[:20],
        "errno_counts": dict(sorted(by_errno.items())),
    }
