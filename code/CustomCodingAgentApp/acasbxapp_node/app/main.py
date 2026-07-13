import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import parse_qs, urlparse

from openclaw_workflow import ProgrammingWorkflow


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the OpenClaw programming multi-agent workflow.")
    parser.add_argument("requirement", nargs="?", help="Programming requirement for the Requirement Agent to refine.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--server", action="store_true", help="Run a small local HTTP server for debugging.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP server host.")
    parser.add_argument("--port", type=int, default=8080, help="HTTP server port.")
    args = parser.parse_args()

    if args.server:
        return _run_server(args.host, args.port)

    if not args.requirement:
        parser.error("requirement is required unless --server is used")

    report = ProgrammingWorkflow().run(args.requirement)

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2))
        return 0

    for result, review in zip(report.results, report.reviews, strict=True):
        print(result.content)
        print(f"Review: approved={review.approved} score={review.score}")
        print("---")
    return 0


def _report_to_dict(report):
    return {
        "requirement": report.requirement,
        "approved": report.approved,
        "results": [
            {"step": result.step, "content": result.content, "metadata": result.metadata}
            for result in report.results
        ],
        "reviews": [
            {"step": review.step, "approved": review.approved, "score": review.score, "findings": review.findings}
            for review in report.reviews
        ],
    }


def _run_server(host: str, port: int) -> int:
    workflow = ProgrammingWorkflow()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send_json({"status": "ok"})
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/workflow":
                self.send_error(404)
                return

            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length).decode("utf-8")
            form = parse_qs(body)
            requirement = form.get("requirement", [""])[0]
            if not requirement:
                self.send_error(400, "requirement is required")
                return

            report = workflow.run(requirement)
            self._send_json(_report_to_dict(report))

        def log_message(self, format: str, *args) -> None:
            return

        def _send_json(self, payload) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Running on http://{host}:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
