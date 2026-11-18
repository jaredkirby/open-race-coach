"""Bounded local intake for synthetic input; no grant or dispatch over HTTP."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from simcoach.live.input_coordinator import SimulatedInput, encoded, label
from simcoach.live.supervisor import strict_json
from simcoach.live.visit_recovery import probe_process


class InputGateway:
    """Trusted Chief binds exact commands; bearer possession identifies one caller.

    Principal names are host assertions, not OS/account authentication. Credentials
    are memory-only and never restored. This object cannot operate a real backend.
    """

    def __init__(self, owner, backend):
        if type(backend) is not SimulatedInput:
            raise ValueError("only_synthetic_input_backend_supported")
        self.owner, self.backend = owner, backend
        self.epoch = uuid4().hex
        native = probe_process(os.getpid())
        if native["state"] != "running" or not native["created_100ns"]:
            raise RuntimeError("input_gateway_native_owner_unknown")
        self.process = {k: native[k] for k in ("pid", "created_100ns")}
        self.lock = threading.RLock()
        self.binding = None
        self.closed = False
        with owner.lock:
            owner.usable()
            if getattr(owner, "gateway_epoch", None):
                raise RuntimeError("input_gateway_already_owned")
            with owner.transaction():
                owner.event("gateway_opened", dict(epoch=self.epoch, process=self.process))
            owner.gateway_epoch = self.epoch

    def bind(self, principal, commands):
        """In-process Chief operation; no new permission, dispatch or recipe inference."""
        label(principal)
        if not isinstance(commands, list) or not 1 <= len(commands) <= 64:
            raise ValueError("input_binding_command_limit")
        # Snapshot before issuing authority so caller mutation cannot change intent.
        payload = encoded(commands)
        if len(payload.encode()) > 65536:
            raise ValueError("input_binding_too_large")
        values = strict_json(payload)
        with self.lock, self.owner.lock:
            self._available()
            generation = self.owner.generation
            if (
                not generation
                or self.owner.cancel.is_set()
                or self.owner.unresolved()
                or self.owner.releasing
                or self.owner.inflight
            ):
                raise ValueError("input_binding_permission_unavailable")
            if self.binding and self.binding["generation"] == generation:
                raise ValueError("input_generation_already_bound")
            selected = {}
            for command in values:
                if (
                    not isinstance(command, dict)
                    or command.get("generation") != generation
                    or not isinstance(command.get("id"), str)
                    or len(command["id"]) != 32
                    or any(c not in "0123456789abcdef" for c in command["id"])
                    or command["id"] in selected
                ):
                    raise ValueError("invalid_input_binding")
                selected[command["id"]] = command
            token = secrets.token_hex(32)
            identity = uuid4().hex
            with self.owner.transaction():
                self.owner.event(
                    "gateway_bound",
                    dict(
                        epoch=self.epoch,
                        binding=identity,
                        principal=principal,
                        generation=generation,
                        commands={
                            key: hashlib.sha256(encoded(value).encode()).hexdigest()
                            for key, value in selected.items()
                        },
                    ),
                )
            self.binding = dict(
                id=identity,
                principal=principal,
                generation=generation,
                token=token,
                commands=selected,
                cancel_result=None,
            )
            return dict(
                schema_version=1,
                owner=self.epoch,
                process=dict(self.process),
                binding=identity,
                principal=principal,
                generation=generation,
                token=token,
            )

    def extend(self, commands):
        """Trusted local continuation after every prior bound command is confirmed.

        Retains the same generation and credential; never reachable over HTTP.
        Fresh observations and reviewed recipe sequencing belong to the compiler.
        """
        if not isinstance(commands, list) or not 1 <= len(commands) <= 64:
            raise ValueError("input_binding_command_limit")
        values = strict_json(encoded(commands))
        with self.lock, self.owner.lock:
            self._available()
            bound = self.binding
            if (
                not bound
                or bound["cancel_result"] is not None
                or self.owner.generation != bound["generation"]
                or self.owner.cancel.is_set()
                or self.owner.inflight
                or self.owner.releasing
                or self.owner.unresolved()
            ):
                raise ValueError("input_extension_permission_unavailable")
            for identity in bound["commands"]:
                prior = self.owner.receipt(identity)
                if not prior or prior["state"] not in {"verified", "skipped"}:
                    raise ValueError("input_extension_previous_unconfirmed")
            selected = dict(bound["commands"])
            added = {}
            for command in values:
                if (
                    not isinstance(command, dict)
                    or command.get("generation") != bound["generation"]
                    or not isinstance(command.get("id"), str)
                    or len(command["id"]) != 32
                    or any(c not in "0123456789abcdef" for c in command["id"])
                    or command["id"] in selected
                ):
                    raise ValueError("invalid_input_extension")
                selected[command["id"]] = command
                added[command["id"]] = hashlib.sha256(encoded(command).encode()).hexdigest()
            if len(selected) > 64 or len(encoded(selected).encode()) > 65536:
                raise ValueError("input_binding_command_limit")
            with self.owner.transaction():
                self.owner.event(
                    "gateway_extended",
                    dict(
                        epoch=self.epoch,
                        binding=bound["id"],
                        principal=bound["principal"],
                        commands=added,
                    ),
                )
            bound["commands"] = selected

    def _available(self):
        if self.closed or getattr(self.owner, "gateway_epoch", None) != self.epoch:
            raise RuntimeError("input_gateway_unavailable")
        self.owner.usable()

    def handle(self, token, request):
        """Small serialized intake/inspection/cancel operations, never dispatch."""
        with self.lock:
            self._available()
            bound = self.binding
            if (
                not bound
                or not isinstance(token, str)
                or len(token) != 64
                or any(c not in "0123456789abcdef" for c in token)
                or not hmac.compare_digest(token, bound["token"])
            ):
                raise PermissionError("input_caller_unverified")
            required = {"schema_version", "owner", "binding", "principal", "op"}
            if not isinstance(request, dict):
                raise ValueError("invalid_input_envelope")
            op = request.get("op")
            if not isinstance(op, str):
                raise ValueError("invalid_input_envelope")
            if op in {"submit", "inspect"}:
                required.add("command_id")
            if (
                set(request) != required
                or type(request.get("schema_version")) is not int
                or request["schema_version"] != 1
                or op not in {"submit", "inspect", "cancel"}
            ):
                raise ValueError("invalid_input_envelope")
            if (
                request["owner"] != self.epoch
                or request["binding"] != bound["id"]
                or request["principal"] != bound["principal"]
            ):
                raise PermissionError("input_binding_mismatch")
            # A new generation cannot be cancelled by an old caller or late reply.
            if self.owner.generation not in {None, bound["generation"]}:
                raise PermissionError("input_generation_replaced")
            if op == "cancel":
                return self._cancel(bound)
            identity = request["command_id"]
            if not isinstance(identity, str) or identity not in bound["commands"]:
                raise PermissionError("input_command_not_bound")
            if op == "submit":
                if (
                    bound["cancel_result"] is not None
                    or self.owner.generation != bound["generation"]
                ):
                    raise PermissionError("input_binding_revoked")
                with self.owner.lock, self.owner.transaction():
                    self.owner.event(
                        "gateway_intake",
                        dict(
                            epoch=self.epoch,
                            binding=bound["id"],
                            principal=bound["principal"],
                            id=identity,
                        ),
                    )
                receipt = self.owner.submit(bound["commands"][identity])
            else:
                receipt = self.owner.receipt(identity)
            return self._response(
                receipt={k: receipt[k] for k in ("id", "fingerprint", "state", "reason")}
                if receipt
                else None
            )

    def _response(self, **value):
        return dict(
            schema_version=1,
            owner=self.epoch,
            process=dict(self.process),
            binding=self.binding["id"],
            principal=self.binding["principal"],
            **value,
        )

    def _cancel(self, bound):
        # Bind check and revocation share the owner's grant lock. Authentication
        # earlier in handle() alone cannot exclude a concurrent Chief context change.
        with self.owner.lock:
            if self.owner.generation not in {None, bound["generation"]}:
                raise PermissionError("input_generation_replaced")
            if bound["cancel_result"] is None:
                # Revoke caller before I/O. An ambiguous cancellation is never retried.
                bound["cancel_result"] = dict(state="unknown", release_confirmed=False)
                try:
                    result = self.owner.takeover(reason="bound_caller_cancel", backend=self.backend)
                    bound["cancel_result"] = dict(
                        state="revoked", release_confirmed=result["synthetic_release_confirmed"]
                    )
                except Exception as error:
                    # No false confirmation after I/O failure; retain a bounded error class.
                    bound["cancel_result"]["error"] = type(error).__name__
        return self._response(cancellation=dict(bound["cancel_result"]))

    def close(self):
        with self.lock:
            if self.closed:
                return
            try:
                # Service closure must revoke even when no credential was issued.
                self.owner.takeover(reason="input_gateway_closing", backend=self.backend)
            finally:
                self.closed = True
                self.binding = None
                with self.owner.lock:
                    if getattr(self.owner, "gateway_epoch", None) == self.epoch:
                        self.owner.gateway_epoch = None


class InputHTTP:
    """Explicitly started loopback-only adapter; no CLI or automatic deployment."""

    def __init__(self, gateway):
        self.gateway = gateway
        slots = threading.BoundedSemaphore(8)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

            def process_request(self, request, client_address):
                if not slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    slots.release()
                    raise

            def process_request_thread(self, request, client_address):
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    slots.release()

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(2)
                super().setup()

            def log_message(self, *_):
                pass  # No credentials or request bodies in access logs.

            def do_POST(self):
                try:
                    if (
                        self.path != "/v1/input"
                        or self.headers.get("Origin") is not None
                        or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                        or self.headers.get("Content-Type") != "application/json"
                        or self.headers.get("Transfer-Encoding") is not None
                        or len(self.headers.get_all("Content-Length", [])) != 1
                        or len(self.headers.get_all("Host", [])) != 1
                        or len(self.headers.get_all("Content-Type", [])) != 1
                        or len(self.headers.get_all("Authorization", [])) != 1
                    ):
                        raise ValueError("invalid_input_http_request")
                    size = int(self.headers["Content-Length"])
                    if not 0 < size <= 4096:
                        raise ValueError("input_request_too_large")
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        raise ValueError("incomplete_input_request")
                    auth = self.headers["Authorization"]
                    if not auth.startswith("Bearer "):
                        raise PermissionError("input_caller_unverified")
                    value = gateway.handle(auth[7:], strict_json(raw.decode("utf8")))
                    status = 200
                except PermissionError as error:
                    status, value = 403, dict(error=str(error))
                except (ValueError, UnicodeError) as error:
                    status, value = 400, dict(error=type(error).__name__)
                except Exception:
                    status, value = 503, dict(error="input_owner_unavailable")
                body = encoded(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        self.server = Server(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/v1/input"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        try:
            self.gateway.close()
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
