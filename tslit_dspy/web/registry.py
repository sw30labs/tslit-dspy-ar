"""Registry of project data, documentation, and functionality.

The command deck uses this to surface *everything* in the TSLIT-DSPy-AR
repo — datasets, compiled models, evaluation reports, GPU-observability
docs, autoresearch assets, and the whitepaper — so nothing is hidden behind
a CLI or a directory listing.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Documentation inventory (markdown/plain text files exposed via /api/docs)
# ---------------------------------------------------------------------------

DOC_TREE: list[dict] = [
    {
        "group": "Project",
        "items": [
            {"label": "README", "path": "README.md"},
            {"label": "AGENTS.md", "path": "AGENTS.md"},
            {"label": "CLAUDE.md", "path": "CLAUDE.md"},
            {"label": "ToDo.md", "path": "ToDo.md"},
        ],
    },
    {
        "group": "Docs",
        "items": [
            {"label": "Elevator Pitch", "path": "docs/elevatorpitch.md"},
            {"label": "Roadmap", "path": "docs/ROADMAP.md"},
            {"label": "Runbook", "path": "docs/RUNBOOK.md"},
            {"label": "Console Output", "path": "docs/console_output.md"},
            {"label": "Phase C Runbook", "path": "docs/RUNBOOK_PHASE_C.md"},
        ],
    },
    {
        "group": "GPU Observability (tslit_hw)",
        "items": [
            {"label": "GPU Obs README", "path": "tslit_hw/README-GPU-OBSERVABILITY.md"},
            {"label": "Spark Runbook", "path": "tslit_hw/RUNBOOK_SPARK_PROTOTYPE.md"},
            {"label": "DGX Handover Prompt", "path": "tslit_hw/HANDOVER_DGX_CODEX_PROMPT.md"},
            {"label": "HLD", "path": "tslit_hw/design-gpu-observability/HLD.md"},
            {"label": "LLD", "path": "tslit_hw/design-gpu-observability/LLD.md"},
            {"label": "DESIGN (spec)", "path": "tslit_hw/design-gpu-observability/DESIGN.md"},
        ],
    },
    {
        "group": "Command Deck",
        "items": [
            {"label": "Command Deck README", "path": "tslit_dspy/web/README.md"},
            {"label": "Web Server (source)", "path": "tslit_dspy/web/server.py"},
            {"label": "Job Runner (source)", "path": "tslit_dspy/web/runner.py"},
            {"label": "Backend Abstraction (source)", "path": "tslit_dspy/backends.py"},
        ],
    },
    {
        "group": "Autoresearch",
        "items": [
            {"label": "Research Program (prompt)", "path": "config/tslit_program.md"},
            {"label": "Agent Loop Source", "path": "scripts/agent_loop_mlx.py"},
            {"label": "Experiment Runner", "path": "scripts/run_experiment.sh"},
        ],
    },
    {
        "group": "Config",
        "items": [
            {"label": "experiment_config.json", "path": "config/experiment_config.json"},
            {"label": ".env.example", "path": ".env.example"},
        ],
    },
    {
        "group": "Whitepaper",
        "items": [
            {"label": "Whitepaper source (.tex)", "path": "whitepaper/manuscript/tslit_dspy_whitepaper.tex"},
            {"label": "Whitepaper Makefile", "path": "whitepaper/Makefile"},
        ],
    },
]

# Files that are JSON/JSONL (rendered as pretty-printed JSON in the UI).
_JSONISH = {".json", ".jsonl"}


def doc_index() -> list[dict]:
    """Return the doc tree with resolved existence flags and sizes."""
    out = []
    for group in DOC_TREE:
        items = []
        for item in group["items"]:
            p = REPO_ROOT / item["path"]
            exists = p.exists()
            items.append({
                "label": item["label"],
                "path": item["path"],
                "exists": exists,
                "size": p.stat().st_size if exists else None,
                "ext": p.suffix,
            })
        out.append({**group, "items": items})
    return out


def read_doc(rel_path: str) -> dict:
    """Return content for a doc path (path traversal-safe)."""
    # Normalize and resolve inside the repo root.
    rel = Path(rel_path)
    if rel.is_absolute():
        return {"error": "absolute paths not allowed"}
    target = (REPO_ROOT / rel).resolve()
    if REPO_ROOT.resolve() not in target.parents and target != REPO_ROOT.resolve():
        return {"error": "path escapes repo root"}
    if not target.exists() or not target.is_file():
        return {"error": "not found"}
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": str(exc)}

    result = {"path": rel_path, "name": target.name, "ext": target.suffix}
    if target.suffix in _JSONISH:
        try:
            result["json"] = json.loads(content)
            result["content"] = content
        except json.JSONDecodeError:
            result["content"] = content
    else:
        result["content"] = content
    result["line_count"] = content.count("\n") + 1
    return result


# ---------------------------------------------------------------------------
# Data inventory
# ---------------------------------------------------------------------------

def data_index() -> dict:
    """Counts and paths for the labeled datasets, compiled model, and evals."""
    data_dir = REPO_ROOT / "workspace" / "data"
    compiled_dir = REPO_ROOT / "workspace" / "compiled"
    eval_dir = REPO_ROOT / "workspace" / "evaluation"

    def jsonl_count(p: Path) -> int | None:
        if not p.exists():
            return None
        return sum(1 for _ in p.open(encoding="utf-8"))

    sets = []
    for name in ("train.jsonl", "dev.jsonl", "test.jsonl",
                 "augmentation_bias_gate_examples.jsonl"):
        p = data_dir / name
        sets.append({
            "name": name, "path": str(p.relative_to(REPO_ROOT)),
            "count": jsonl_count(p), "exists": p.exists(),
        })

    compiled = []
    for p in sorted(compiled_dir.glob("*.json")):
        compiled.append({
            "name": p.name,
            "path": str(p.relative_to(REPO_ROOT)),
            "size": p.stat().st_size,
        })

    evals = []
    for p in sorted(eval_dir.glob("*.json")):
        evals.append({
            "name": p.name,
            "path": str(p.relative_to(REPO_ROOT)),
            "size": p.stat().st_size,
        })

    return {
        "datasets": sets,
        "compiled_models": compiled,
        "eval_reports": evals,
    }


def load_eval_json(name: str) -> dict | None:
    """Load a specific evaluation JSON report by basename."""
    eval_dir = REPO_ROOT / "workspace" / "evaluation"
    if "/" in name or ".." in name:
        return None
    p = eval_dir / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Compiled model inspection (reuses DSPyAnalyzerAdapter.inspect_compiled)
# ---------------------------------------------------------------------------

def compiled_inspection(compiled_path: str) -> str:
    from tslit_dspy.adapter import DSPyAnalyzerAdapter

    return DSPyAnalyzerAdapter.inspect_compiled(compiled_path)


def project_meta() -> dict:
    """Basic project metadata + OMLX model defaults for the header badges."""
    from tslit_dspy import __version__

    return {
        "version": __version__,
        "name": "TSLIT-DSPy-AR",
        "repo": str(REPO_ROOT),
        "default_model": "DeepSeek-V4-Flash-0731-MLX",
        "default_base_url": "http://127.0.0.1:8000/v1",
    }