from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from gus_reporter.config import AppConfig, resolve_report_output_dir
from gus_reporter.models import ReportRequest
from gus_reporter.pipeline import ReportPipeline
from gus_reporter.ui_state import RunStateStore


PACKAGE_ROOT = Path(__file__).resolve().parent
WEB_ROOT = PACKAGE_ROOT / "web"
UI_FOCUS_AREAS = ["financials", "legal", "news"]


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class GusUIApplication:
    def __init__(self) -> None:
        self.store = RunStateStore(resolve_report_output_dir())

    def _missing_runtime_requirements(self) -> list[str]:
        missing: list[str] = []
        if not os.getenv("OPENAI_API_KEY", "").strip():
            missing.append("OPENAI_API_KEY")
        if not os.getenv("TINYFISH_API_KEY", "").strip():
            missing.append("TINYFISH_API_KEY")
        return missing

    def health_payload(self) -> dict[str, Any]:
        missing = self._missing_runtime_requirements()
        return {
            "openai_api_key_present": "OPENAI_API_KEY" not in missing,
            "tinyfish_api_key_present": "TINYFISH_API_KEY" not in missing,
            "missing_requirements": missing,
            "company_name_only_supported": True,
            "notes": [
                "The UI is live and the financial, legal, and news pipelines are wired end to end.",
                "Company-name-only discovery now seeds web search acquisition paths and has strong resolver logic.",
                "Runtime becomes fully live when both OpenAI and Tinyfish keys are present.",
            ],
        }

    def start_run(self, payload: dict[str, Any]) -> str:
        missing = self._missing_runtime_requirements()
        if missing:
            raise ValueError(
                "Missing runtime configuration: " + ", ".join(missing)
            )
        request_payload = self._normalize_request_payload(payload)
        run_id = self.store.create_run(request_payload)
        worker = threading.Thread(
            target=self._run_pipeline,
            args=(run_id, request_payload),
            daemon=True,
            name=f"gus-ui-run-{run_id}",
        )
        worker.start()
        return run_id

    def _run_pipeline(self, run_id: str, request_payload: dict[str, Any]) -> None:
        try:
            config = AppConfig.from_env()
            request = ReportRequest.from_dict(
                request_payload, config.tinyfish_default_browser_profile
            )
            pipeline = ReportPipeline(
                config,
                progress_callback=lambda event: self.store.record_event(run_id, event),
            )
            pipeline.run(request)
        except Exception as exc:
            run = self.store.get_run(run_id) or {}
            if run.get("status") != "failed":
                self.store.record_event(
                    run_id,
                    {
                        "event": "pipeline_failed",
                        "run_dir": str(run.get("run_dir", "")).strip(),
                        "stage": str(run.get("current_stage", "planning")).strip()
                        or "planning",
                        "error": str(exc),
                    },
                )
            return

    def _normalize_request_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        company_name = str(payload.get("company_name", "")).strip()
        if not company_name:
            raise ValueError("company_name is required")

        financial_source_urls = payload.get("financial_source_urls", [])
        if isinstance(financial_source_urls, str):
            financial_source_urls = [
                line.strip()
                for line in financial_source_urls.replace(",", "\n").splitlines()
                if line.strip()
            ]

        credential_item_ids = payload.get("tinyfish_credential_item_ids", [])
        if isinstance(credential_item_ids, str):
            credential_item_ids = [
                line.strip()
                for line in credential_item_ids.replace(",", "\n").splitlines()
                if line.strip()
            ]

        return {
            "company_name": company_name,
            "company_website": str(payload.get("company_website", "")).strip(),
            "primary_jurisdiction": str(payload.get("primary_jurisdiction", "")).strip(),
            "ticker": str(payload.get("ticker", "")).strip(),
            "investor_relations_url": str(
                payload.get("investor_relations_url", "")
            ).strip(),
            "financial_source_urls": financial_source_urls,
            "tinyfish_use_vault": _coerce_bool(payload.get("tinyfish_use_vault", False)),
            "tinyfish_credential_item_ids": credential_item_ids,
            "tinyfish_enable_agent_memory": _coerce_bool(
                payload.get("tinyfish_enable_agent_memory", True)
            ),
            "report_goal": str(
                payload.get(
                    "report_goal",
                    "Generate a source-backed multi-domain diligence report across financial, legal, and news evidence with no unsupported claims.",
                )
            ).strip(),
            "focus_areas": list(UI_FOCUS_AREAS),
        }

    def read_artifact(self, run_id: str, relative_path: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        run_dir_value = self.store.resolve_run_dir(run_id)
        if run is None and not run_dir_value:
            raise FileNotFoundError("Unknown run id")
        if not run_dir_value:
            raise FileNotFoundError("Run directory is not available yet")
        run_dir = Path(run_dir_value)
        requested = Path(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise ValueError("Invalid artifact path")
        artifact_path = (run_dir / requested).resolve()
        if not artifact_path.exists():
            raise FileNotFoundError(f"Artifact not found: {relative_path}")
        if run_dir.resolve() not in artifact_path.parents and artifact_path != run_dir.resolve():
            raise ValueError("Artifact path escaped run directory")
        raw = artifact_path.read_text(encoding="utf-8")
        try:
            content = json.loads(raw)
        except json.JSONDecodeError:
            content = raw
        return {
            "relative_path": relative_path,
            "content": content,
        }


class GusUIRequestHandler(BaseHTTPRequestHandler):
    server_version = "GUSUI/0.1"

    @property
    def app(self) -> GusUIApplication:
        return self.server.app  # type: ignore[attr-defined]

    def _is_authorized(self) -> bool:
        expected_token = os.environ.get("GUS_UI_TOKEN")
        if not expected_token:
            return True # No token configured, allow all

        auth_header = self.headers.get("Authorization")
        if not auth_header:
            return False

        if not auth_header.startswith("Bearer "):
            return False

        token = auth_header[7:].strip()
        return hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        # Always serve static files without auth
        if parsed.path == "/":
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if parsed.path == "/app.js":
            return self._serve_static("app.js", "application/javascript; charset=utf-8")
        if parsed.path == "/styles.css":
            return self._serve_static("styles.css", "text/css; charset=utf-8")

        if parsed.path == "/api/auth/status":
            expected_token = os.environ.get("GUS_UI_TOKEN")
            if not expected_token:
                return self._write_json({"auth_required": False})
            else:
                return self._write_json({"auth_required": True, "authenticated": self._is_authorized()})


        if not self._is_authorized():
            return self._write_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)

        if parsed.path == "/api/health":
            return self._write_json(self.app.health_payload())
        if parsed.path == "/api/runs":
            return self._write_json({"runs": self.app.store.list_runs()})
        if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/artifact"):
            run_id = parsed.path.split("/")[3]
            relative_path = parse_qs(parsed.query).get("path", [""])[0]
            try:
                artifact = self.app.read_artifact(run_id, unquote(relative_path))
            except Exception as exc:
                return self._write_json(
                    {"error": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return self._write_json(artifact)
        if parsed.path.startswith("/api/runs/"):
            run_id = parsed.path.split("/")[3]
            run = self.app.store.get_run(run_id)
            if run is None:
                return self._write_json(
                    {"error": f"Unknown run id: {run_id}"},
                    status=HTTPStatus.NOT_FOUND,
                )
            return self._write_json(run)
        return self._write_json(
            {"error": f"Unknown path: {parsed.path}"},
            status=HTTPStatus.NOT_FOUND,
        )

    def do_POST(self) -> None:  # noqa: N802
        if not self._is_authorized():
            return self._write_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)

        parsed = urlparse(self.path)
        if parsed.path != "/api/runs":
            return self._write_json(
                {"error": f"Unknown path: {parsed.path}"},
                status=HTTPStatus.NOT_FOUND,
            )

        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._write_json(
                {"error": "Request body must be valid JSON."},
                status=HTTPStatus.BAD_REQUEST,
            )

        try:
            run_id = self.app.start_run(payload)
        except Exception as exc:
            return self._write_json(
                {"error": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )

        run = self.app.store.get_run(run_id)
        return self._write_json(
            {"run_id": run_id, "run": run},
            status=HTTPStatus.ACCEPTED,
        )

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _serve_static(self, filename: str, content_type: str) -> None:
        path = WEB_ROOT / filename
        if not path.exists():
            return self._write_json(
                {"error": f"Static asset missing: {filename}"},
                status=HTTPStatus.NOT_FOUND,
            )
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(data)

    def _write_json(
        self,
        payload: dict[str, Any] | list[Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)


def build_server(host: str, port: int) -> ThreadingHTTPServer:
    app = GusUIApplication()
    server = ThreadingHTTPServer((host, port), GusUIRequestHandler)
    server.app = app  # type: ignore[attr-defined]
    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local GUS operator UI server."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = build_server(args.host, args.port)
    print(f"GUS UI running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down GUS UI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
