"""Stdlib HTTP server for the session viewer.

    python -m demo.server            # http://localhost:8000
    python -m demo.server --no-live  # recordings only, never touches the agent

Recordings load instantly and always work. "Run live" builds nothing new - the
`Agent` and catalog index are constructed once at startup and reused - so a live
run is one session's worth of work, not a cold start. If a live run fails for any
reason it falls back to the recording rather than blanking the page.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).parent / "static"
RECORDINGS = Path(__file__).parent / "recordings"


class DemoState:
    """Owns the agent and the catalog data the runner needs.

    The agent's lexical index is an in-memory SQLite connection, and SQLite
    connections are bound to the thread that opened them. `ThreadingHTTPServer`
    serves every request on a fresh thread, so the agent lives on one dedicated
    worker instead and all live work is submitted to it. Without this the agent
    does not crash - it degrades to the `empty` fallback tier and returns no
    recommendations, which is correct behaviour and very easy to miss.
    """

    def __init__(self, catalog: str, dataset: str, results: str, live: bool) -> None:
        self.catalog = catalog
        self.dataset = dataset
        self.results = REPO / results
        self.live = live
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent")
        self._agent = None
        self._samples: dict[str, dict] = {}
        self._catalog_ids: set[str] = set()
        self._categories: dict[str, list[str]] = {}
        self._products: dict[str, dict] = {}
        self._runs = 0

    def warm(self) -> None:
        """Pay the index cost at startup, not on the first click during a demo."""
        if self.live:
            self._worker.submit(self._build).result()

    def close(self) -> None:
        self._worker.shutdown(wait=False)

    def _build(self) -> None:
        if self._agent is not None:
            return
        from evaluator.local_evaluator import catalog_index, load_jsonl
        from starter.agent import Agent

        self._samples = {
            str(sample["sample_id"]): sample for sample in load_jsonl(self.dataset)
        }
        self._catalog_ids, self._categories, self._products = catalog_index(self.catalog)
        self._agent = Agent(self.catalog)

    def _run(self, sample_id: str) -> dict:
        from demo.session_runner import run_session

        self._build()
        sample = self._samples.get(sample_id)
        if sample is None:
            raise KeyError(sample_id)
        self._runs += 1
        # A fresh session id each run keeps traces from colliding in the
        # agent-scoped tracer ring.
        return run_session(
            self._agent,
            sample,
            self._catalog_ids,
            self._categories,
            self._products,
            session_id=f"live_{sample_id}_{self._runs}",
        )

    def run_live(self, sample_id: str) -> dict:
        payload = self._worker.submit(self._run, sample_id).result()
        payload["live"] = True
        return payload

    def metrics(self) -> dict:
        """Read the reported numbers off disk. Never recompute them here.

        A dashboard that derives its own score can disagree with the score in the
        report; this one cannot.
        """
        payload: dict = {"current": None, "baseline": None, "stale": False}
        if self.results.exists():
            data = json.loads(self.results.read_text(encoding="utf-8"))
            payload["current"] = {key: value for key, value in data.items() if key != "sessions"}
        baseline_path = REPO / "docs" / "baseline_results.json"
        if baseline_path.exists():
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            payload["baseline"] = {key: value for key, value in baseline.items() if key != "sessions"}
        return payload


class Handler(BaseHTTPRequestHandler):
    state: DemoState

    server_version = "TechJamDemo/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # the console belongs to the presenter

    # -- responses ---------------------------------------------------------

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _static(self, relative: str) -> None:
        target = (STATIC / relative).resolve()
        if not target.is_file() or STATIC.resolve() not in target.parents:
            self._error(HTTPStatus.NOT_FOUND, f"no such file: {relative}")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type = f"{content_type}; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), content_type)

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path.startswith("/static/"):
            self._static(path[len("/static/"):])
        elif path == "/api/metrics":
            self._json({**self.state.metrics(), "live_enabled": self.state.live})
        elif path == "/api/sessions":
            manifest = RECORDINGS / "index.json"
            if not manifest.exists():
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "no recordings - run: python -m demo.recorder",
                )
                return
            self._send(
                HTTPStatus.OK, manifest.read_bytes(), "application/json; charset=utf-8"
            )
        elif path.startswith("/api/session/"):
            self._recorded(path[len("/api/session/"):])
        else:
            self._error(HTTPStatus.NOT_FOUND, f"no route for {path}")

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.startswith("/api/run/"):
            self._error(HTTPStatus.NOT_FOUND, f"no route for {path}")
            return
        sample_id = path[len("/api/run/"):]
        if not self.state.live:
            self._recorded(sample_id, note="live mode is off (--no-live)")
            return
        try:
            self._json(self.state.run_live(sample_id))
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, f"unknown sample: {sample_id}")
        except Exception as exc:  # never blank the screen mid-demo
            self._recorded(sample_id, note=f"live run failed: {type(exc).__name__}: {exc}")

    def _recorded(self, sample_id: str, note: str | None = None) -> None:
        target = (RECORDINGS / f"{sample_id}.json").resolve()
        if not target.is_file() or RECORDINGS.resolve() not in target.parents:
            self._error(HTTPStatus.NOT_FOUND, f"no recording for {sample_id}")
            return
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["live"] = False
        if note:
            payload["note"] = note
        self._json(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Session viewer for the shopping agent")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--results", default="results.json")
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="serve recordings only; never construct the agent",
    )
    args = parser.parse_args()

    Handler.state = DemoState(args.catalog, args.dataset, args.results, live=not args.no_live)
    if not args.no_live:
        print("building the retrieval index (once)...")
        Handler.state.warm()
        print("agent ready")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"\n  http://{args.host}:{args.port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        server.server_close()
        Handler.state.close()


if __name__ == "__main__":
    main()
