"""Tests for the TSLIT-DSPy command deck (web package).

These exercise the stdlib HTTP server and its JSON API without requiring a
live OMLX backend — the background job runner is stubbed so the deck boots
and serves SPA + data + docs deterministically in CI.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
import requests

from tslit_dspy.web import registry, server
from tslit_dspy.web import runner as runner_mod


@pytest.fixture(scope="module")
def client():
    """Start the deck on an ephemeral port with the job runner stubbed."""
    # Stub the runner so no LLM calls are made.
    orig_start_job = runner_mod.start_job

    def fake_start_job(params):
        return "tslit-testjob", None

    runner_mod.start_job = fake_start_job

    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    base = f"http://127.0.0.1:{port}"
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()
        runner_mod.start_job = orig_start_job


def test_health(client):
    d = requests.get(f"{client}/api/health", timeout=5).json()
    assert d["ok"] is True
    assert d["busy"] is False
    assert "meta" in d


def test_index_served(client):
    r = requests.get(f"{client}/", timeout=5)
    assert r.status_code == 200
    assert "TSLIT-DSPy" in r.text
    assert "<script>" in r.text


def test_data_index(client):
    d = requests.get(f"{client}/api/data", timeout=5).json()
    assert len(d["datasets"]) >= 4
    names = {s["name"] for s in d["datasets"]}
    assert {"train.jsonl", "dev.jsonl", "test.jsonl"} <= names


def test_docs_index(client):
    d = requests.get(f"{client}/api/docs", timeout=5).json()
    groups = d["groups"]
    assert any(g["group"] == "Project" for g in groups)
    all_items = [i for g in groups for i in g["items"]]
    assert any(i["path"] == "README.md" for i in all_items)


def test_doc_content(client):
    d = requests.get(
        f"{client}/api/docs/content", params={"path": "docs/RUNBOOK_PHASE_C.md"},
        timeout=5,
    ).json()
    assert "error" not in d
    assert "Phase C" in d["content"]


def test_doc_traversal_blocked(client):
    d = requests.get(
        f"{client}/api/docs/content", params={"path": "../.env"},
        timeout=5,
    ).json()
    assert "error" in d


def test_compiled_inspection(client):
    d = requests.get(
        f"{client}/api/compiled/inspect",
        params={"path": "workspace/compiled/tslit_analyzer_optimized.json"},
        timeout=5,
    ).json()
    assert "inspection" in d
    assert "COMPILED MODEL INSPECTION" in d["inspection"]


def test_run_job_stubbed(client):
    r = requests.post(
        f"{client}/api/run", json={"kind": "probe", "response_text": "x"},
        timeout=5,
    )
    assert r.status_code == 202
    assert r.json()["job_id"] == "tslit-testjob"


def test_append_validation(client):
    # invalid JSON lines should be rejected by the client, but server rejects empty
    r = requests.post(
        f"{client}/api/append", json={"lines": [], "target": "train.jsonl"},
        timeout=5,
    )
    assert r.status_code == 400
    # traversal blocked server-side
    r2 = requests.post(
        f"{client}/api/append", json={"lines": [{}], "target": "../evil.jsonl"},
        timeout=5,
    )
    assert r2.status_code == 400


def test_registry_repo_root():
    assert (registry.REPO_ROOT / "pyproject.toml").exists()
    assert (registry.REPO_ROOT / "tslit_dspy" / "web").is_dir()