"""LLM backend helpers for the TSLIT-DSPy command deck.

Re-exports the shared backend abstraction and adds web-side conveniences
(HTTP reachability and model listing) so the SPA can probe and introspect
the local OMLX server exactly like Book Buddy does.
"""

from __future__ import annotations

from tslit_dspy.backends import (
    BackendConfigError,
    DEFAULT_OMLX_URL,
    api_key_for,
    normalize_backend,
    omlx_lm,
)

__all__ = [
    "BackendConfigError",
    "DEFAULT_OMLX_URL",
    "api_key_for",
    "normalize_backend",
    "omlx_lm",
    "backend_reachable",
    "list_models",
]


def backend_reachable(
    backend: str = "omlx",
    base_url: str | None = None,
    timeout: float = 1.5,
) -> dict:
    """Reachability of the configured LLM endpoint (Book Buddy contract).

    Returns a dict with ``reachable`` plus connection details / error text.
    """
    import socket
    from urllib.parse import urlparse

    try:
        name = normalize_backend(backend)
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "backend": backend,
                "base_url": base_url, "detail": str(e)}
    url = (base_url or DEFAULT_OMLX_URL) if name == "omlx" else (base_url or "")
    if not url:
        return {"reachable": False, "backend": name, "base_url": base_url,
                "detail": "base_url required for vllm"}
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"reachable": True, "backend": name, "base_url": url,
                    "host": host, "port": port}
    except OSError as e:
        return {"reachable": False, "backend": name, "base_url": url,
                "host": host, "port": port, "detail": str(e)}


def list_models(backend: str = "omlx", base_url: str | None = None) -> list[dict]:
    """List models served by the configured backend (GET /models)."""
    import json
    import socket
    import urllib.request
    from urllib.parse import urlparse

    try:
        name = normalize_backend(backend)
    except BackendConfigError:
        return []
    url = (base_url or DEFAULT_OMLX_URL) if name == "omlx" else (base_url or "")
    if not url:
        return []
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=1.5):
            pass
    except OSError:
        return []
    req = urllib.request.Request(
        f"{url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key_for(name)}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = resp.read().decode("utf-8")
    except Exception:  # noqa: BLE001
        return []
    try:
        payload = json.loads(data)
        return [{"id": m.get("id")} for m in payload.get("data", []) if m.get("id")]
    except (json.JSONDecodeError, AttributeError):
        return []