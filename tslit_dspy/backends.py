"""LLM backend abstraction — OMLX (Mac) or vLLM (DGX).

Mirrors Contingency Atlas / Book Buddy provider naming:

  - ``omlx`` (alias ``mlx``) — local OMLX server, default ``http://127.0.0.1:8000/v1``
  - ``vllm`` (alias ``dgx``) — DGX / vLLM, ``--base-url`` required

Both backends are OpenAI-compatible and are consumed through DSPy's ``LM``
so the compiled TSLITAnalyzer can run against them unchanged.

OMLX is non-streaming only (streaming hangs on large models).

Author: Nic Cravino — TSLIT-DSPy
License: Apache-2.0
"""

from __future__ import annotations

import os
from typing import ClassVar, Literal

__all__ = [
    "BackendName",
    "BackendConfigError",
    "DEFAULT_OMLX_URL",
    "normalize_backend",
    "api_key_for",
    "omlx_lm",
]

BackendName = Literal["omlx", "vllm"]

DEFAULT_OMLX_URL = "http://127.0.0.1:8000/v1"

# Canonical names + Contingency Atlas-style aliases.
_PROVIDER_ALIASES: dict[str, str] = {
    "mlx": "omlx",
    "dgx": "vllm",
}
_VALID_BACKENDS: tuple[str, ...] = ("omlx", "vllm")


class BackendConfigError(ValueError):
    """Raised when backend selection/config is invalid."""


def normalize_backend(value: str | None) -> str:
    """Normalize a backend name; raise BackendConfigError if unknown."""
    if not value:
        raise BackendConfigError(
            "--backend is required (one of: omlx, vllm; aliases: mlx, dgx)."
        )
    name = value.strip().lower()
    if name == "ollama":
        raise BackendConfigError(
            "backend 'ollama' is no longer supported — use --backend omlx "
            "(OpenAI-compatible OMLX at http://127.0.0.1:8000/v1)."
        )
    name = _PROVIDER_ALIASES.get(name, name)
    if name not in _VALID_BACKENDS:
        raise BackendConfigError(
            f"Unknown backend: {value!r}. Valid: omlx, vllm "
            f"(aliases: {', '.join(sorted(_PROVIDER_ALIASES))})."
        )
    return name


def api_key_for(backend: str) -> str:
    """API key for a normalized backend name."""
    if backend == "vllm":
        return os.environ.get("VLLM_API_KEY") or os.environ.get("DGX_API_KEY") or "EMPTY"
    return os.environ.get("OMLX_API_KEY", "test") or "test"


def omlx_lm(model: str, base_url: str | None = None):
    """Return a DSPy LM configured against the OMLX server.

    Lazy-imports dspy so the stdlib-only command deck still boots when the
    heavy inference stack is not installed.
    """
    import dspy

    base = base_url or DEFAULT_OMLX_URL
    return dspy.LM(
        f"openai/{model}",
        api_base=base,
        api_key=api_key_for("omlx"),
        cache=False,
    )