"""Serve a separate private read-only live page on loopback; no route installation."""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from simcoach.cli.telemetry_watch import write_status
from simcoach.live.dashboard import DashboardFeed, read_source
from simcoach.live.dashboard_feedback import reviewed_feedback
from simcoach.live.dashboard_operations import OperationsDisplay
from simcoach.live.supervisor import number
from simcoach.live.visit_recovery import probe_process
from simcoach.live.visit_release import active_binding


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 4

    def __init__(self, address, feed, token, assets, *, clock=time.perf_counter):
        if address[0] != "127.0.0.1":
            raise ValueError("loopback_required_private_route_not_configured")
        self.feed, self.token, self.assets, self.clock = feed, token, assets, clock
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, DashboardHandler)
        self.timeout = .1

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(2)
        return request, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Do not log requests, authorization headers, client addresses or payloads.
        pass


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ORC"
    sys_version = ""

    def log_message(self, *_):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, b'{"error":"request_rejected"}', "application/json")

    def reply(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def do_GET(self):
        host = self.headers.get("Host", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in {"localhost", "127.0.0.1"}:
            self.send_error(403)
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host, "https://" + host}:
            self.send_error(403)
            return
        if self.path in self.server.assets:
            body, kind = self.server.assets[self.path]
            self.reply(200, body, kind)
            return
        if self.path != "/v1/telemetry":
            self.send_error(404)
            return
        authorization = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.token
        if not hmac.compare_digest(authorization.encode("utf8"), expected.encode("ascii")):
            self.send_error(401)
            return
        nonce = self.headers.get("X-ORC-Request-ID", "")
        if not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", nonce):
            self.send_error(400)
            return
        value = self.server.feed.response(nonce, elapsed=self.server.clock())
        self.reply(200, json.dumps(value, allow_nan=False).encode("utf8"), "application/json")


def serve(archive, out, *, port=0, duration=3600, visit=None, operations=None):
    duration = number(duration, minimum=.1, maximum=86400)
    archive, out = Path(archive).absolute(), Path(out).absolute()
    if (archive.resolve() != archive or out.resolve() != out
            or out == archive or archive in out.parents or out in archive.parents):
        raise ValueError("explicit_separate_source_and_output_required")
    out.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(32)
    with (out / "connection-code.txt").open("x", encoding="ascii") as stream:
        stream.write(token)
    docs = Path(__file__).resolve().parents[2] / "docs"
    assets = {"/": ((docs / "live-dashboard.html").read_bytes(), "text/html; charset=utf-8"),
              "/live-dashboard.mjs": ((docs / "live-dashboard.mjs").read_bytes(),
                                      "text/javascript; charset=utf-8")}
    feed = DashboardFeed()
    operation_view = OperationsDisplay(operations, archive)
    selection = Path(visit) / "output/dashboard-selection.json" if visit else None
    with DashboardServer(("127.0.0.1", port), feed, token, assets) as server:
        write_status(out / "run.json", dict(schema_version=1, mode="read_only_loopback",
            archive=str(archive), publisher_id=feed.publisher_id,
            process=probe_process(os.getpid()),
            source_release=active_binding(),
            started_at=time.time(), address="127.0.0.1", port=server.server_port,
            private_mac_route="NOT RUN", live_acceptance="NOT RUN"))
        start = last_sample = last_publish = time.perf_counter()
        count = 0
        while time.perf_counter() - start < duration and not (out / "stop").exists():
            elapsed = time.perf_counter()
            if elapsed - last_sample >= .25 or count == 0:
                identity, status, error = None, None, None
                try:
                    identity, status = read_source(archive)
                except (OSError, ValueError, TypeError, RecursionError):
                    error = "source_unavailable"
                # Timestamp after I/O so a slow read cannot make old evidence fresh.
                feed.sample(status, source_id=identity, now=time.time(),
                            elapsed=time.perf_counter(), read_error=error)
                feedback = reviewed_feedback(visit, selection)
                observed = time.perf_counter()
                supervision = operation_view.observe(
                    feed.response("internal-observation", elapsed=observed),
                    now=time.time(), elapsed=observed)
                # Keep the pre-read monotonic anchor; slow I/O cannot extend expiry.
                feed.panels(feedback, supervision, elapsed=observed)
                last_sample = time.perf_counter()
                count += 1
            if elapsed - last_publish >= 1:
                write_status(out / "status.json", dict(schema_version=1, state="serving",
                    at=time.time(), samples=count, publisher_id=feed.publisher_id,
                    mac_route_verified=False))
                last_publish = elapsed
            server.handle_request()
        write_status(out / "status.json", dict(schema_version=1, state="stopped", at=time.time(),
                                               samples=count, mac_route_verified=False))
    return 0


def main(argv=None):
    from simcoach.live.visit_release import require_guarded_entry
    require_guarded_entry()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--duration", type=float, default=3600)
    parser.add_argument("--visit", type=Path)
    parser.add_argument("--operations", type=Path)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("invalid port")
    return serve(args.archive, args.out, port=args.port, duration=args.duration,
                 visit=args.visit, operations=args.operations)


if __name__ == "__main__":
    raise SystemExit(main())
