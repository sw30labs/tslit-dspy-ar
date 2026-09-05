"""Web command deck server for TSLIT-DSPy.

Stdlib-only HTTP server (no new dependencies). Serves the single-page app
plus a JSON API that surfaces the full TSLIT-DSPy-AR workbench: the
application-layer detection pipeline, the GPU-observability channel, the
autoresearch agent, project data, and all documentation.

Run:
    python -m tslit_dspy.web            # http://127.0.0.1:8780
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import ipaddress
import json
import logging
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from tslit_dspy import __version__
from tslit_dspy.web import registry, runner

__all__ = ["main", "serve", "DashboardHandler", "STATIC_DIR"]

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 512_000


def _is_loopback_bind_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _client_is_loopback(handler: BaseHTTPRequestHandler) -> bool:
    host = handler.client_address[0]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "127.0.0.1", "::1")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "TSLITCommandDeck/0.1"

    def _local_request_allowed(self) -> bool:
        return _client_is_loopback(self)

    def _mutation_request_allowed(self) -> bool:
        return _client_is_loopback(self)

    # -- GET -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/") and not self._local_request_allowed():
            self._send_json({"error": "dashboard APIs are restricted to loopback"},
                            status=403)
            return

        query = parse_qs(urlparse(self.path).query)

        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/favicon.svg":
            self._send_file(STATIC_DIR / "favicon.svg", "image/svg+xml")

        # ---- core APIs ----
        elif path == "/api/health":
            self._send_json({
                "ok": True, "version": __version__, "busy": runner.busy(),
                "active_job_id": runner.active_job_id(),
                "meta": registry.project_meta(),
            })
        elif path == "/api/backend":
            backend = str(query.get("backend", ["omlx"])[0])
            base_url = query.get("base_url", [None])[0]
            self._send_json(runner.backend_reachable(backend, base_url))
        elif path == "/api/models":
            backend = str(query.get("backend", ["omlx"])[0])
            base_url = query.get("base_url", [None])[0]
            from tslit_dspy.web.backends import list_models

            self._send_json({"models": list_models(backend, base_url)})
        elif path == "/api/state":
            snap = runner.current_snapshot()
            self._send_json(snap if snap is not None else {"empty": True})
        elif path == "/api/events":
            try:
                limit = int(query.get("limit", ["120"])[0])
            except ValueError:
                limit = 120
            self._send_json({"events": runner.live_events(limit=limit),
                             "busy": runner.busy()})
        elif path == "/api/jobs":
            self._send_json({"jobs": runner.list_jobs(), "busy": runner.busy()})
        elif path.startswith("/api/jobs/"):
            job = runner.get_job(path.rsplit("/", 1)[-1])
            if job:
                self._send_json(job)
            else:
                self._send_json({"error": "unknown job"}, status=404)

        # ---- data / docs / functionality ----
        elif path == "/api/data":
            self._send_json(registry.data_index())
        elif path == "/api/docs":
            self._send_json({"groups": registry.doc_index()})
        elif path.startswith("/api/docs/content"):
            rel = query.get("path", [""])[0]
            self._send_json(registry.read_doc(rel))
        elif path.startswith("/api/eval/"):
            name = path.rsplit("/", 1)[-1]
            self._send_json(registry.load_eval_json(name) or {"error": "not found"},
                            status=200 if registry.load_eval_json(name) else 404)
        elif path.startswith("/api/compiled/inspect"):
            rel = query.get("path", ["workspace/compiled/tslit_analyzer_optimized.json"])[0]
            self._send_json({"inspection": registry.compiled_inspection(rel)})

        else:
            self.send_error(404, "Not found")

    # -- POST ------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if not self._mutation_request_allowed():
            self._send_json({"error": "run requests are restricted to the local dashboard"},
                            status=403)
            return
        if path == "/api/run":
            self._handle_run()
            return
        if path == "/api/append":
            self._handle_append()
            return
        self.send_error(404, "Not found")

    # -- request body helpers --------------------------------------------
    def _read_json_body(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type != "application/json":
            return None, "Content-Type must be application/json"
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None, "Content-Length is required"
        try:
            length = int(raw_length)
        except ValueError:
            return None, "invalid Content-Length"
        if length < 0 or length > MAX_REQUEST_BYTES:
            return None, "invalid Content-Length"
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "invalid JSON body"
        if not isinstance(body, dict):
            return None, "JSON body must be an object"
        return body, None

    def _handle_run(self) -> None:
        body, err = self._read_json_body()
        if err:
            self._send_json({"error": err}, status=400)
            return
        job_id, error = runner.start_job(body)
        if error:
            status = 409 if "running" in error else 400
            self._send_json({"error": error}, status=status)
        else:
            self._send_json({"job_id": job_id}, status=202)

    def _handle_append(self) -> None:
        """Append JSONL lines to workspace/data/train.jsonl (augmentation)."""
        body, err = self._read_json_body()
        if err:
            self._send_json({"error": err}, status=400)
            return
        lines = body.get("lines")
        target = body.get("target", "workspace/data/train.jsonl")
        if not isinstance(lines, list) or not lines:
            self._send_json({"error": "lines must be a non-empty array"}, status=400)
            return
        import re

        if "/" in target or ".." in target or "\\" in target:
            self._send_json({"error": "invalid target"}, status=400)
            return
        if not target.endswith(".jsonl"):
            self._send_json({"error": "target must end in .jsonl"}, status=400)
            return
        data_dir = registry.REPO_ROOT / "workspace" / "data"
        path = (data_dir / target).resolve()
        if data_dir.resolve() not in path.parents:
            self._send_json({"error": "target escapes data dir"}, status=400)
            return
        written = 0
        with path.open("a", encoding="utf-8") as f:
            for item in lines:
                if not isinstance(item, dict):
                    continue
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                written += 1
        self._send_json({"appended": written, "target": target}, status=200)

    # -- response helpers ------------------------------------------------
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def serve(*, host: str = "127.0.0.1", port: int = 8780,
          open_browser: bool = True) -> None:
    """Start the command deck server (blocks until Ctrl-C)."""
    if not _is_loopback_bind_host(host):
        raise SystemExit(
            "--host must be a loopback address or localhost; "
            "the command deck is intentionally local-only"
        )
    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"Port {port} is already in use — a command deck is likely")
            print(f"already running at http://{host}:{port}")
            raise SystemExit(1) from None
        raise
    url = f"http://{host}:{port}"
    print(f"TSLIT-DSPy command deck → {url}  (Ctrl+C to stop)")
    if open_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="TSLIT-DSPy web command deck")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open a browser tab on start.")
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()