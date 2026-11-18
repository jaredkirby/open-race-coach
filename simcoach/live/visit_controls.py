"""Explicitly opened local controls for an existing visit; no simulator ownership."""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path
from uuid import uuid4

from simcoach.cli.race_watch import SourceLock
from simcoach.cli.telemetry_watch import write_status
from simcoach.live.capture_gate import published_capture_ready
from simcoach.live.supervisor import strict_json
from simcoach.live.visit_clock import REASONS


def _read(path):
    try:
        value = strict_json(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def _fresh(value, now):
    try:
        return (type(value) in (int, float) and math.isfinite(value)
                and 0 <= now-value < 3)
    except (OverflowError, TypeError):
        return False


class VisitControls:
    """Separate-process client. A request is not proof of mute or departure handling."""

    def __init__(self, run: Path, *, clock=time.time, elapsed=time.perf_counter):
        self.run = Path(run).resolve(strict=True)
        if not all((self.run/name).is_dir() for name in ("requests", "receipts")):
            raise ValueError("not_a_visit_directory")
        self.clock, self.elapsed = clock, elapsed

    def status(self):
        state = _read(self.run/"status.json")
        heartbeat = _read(self.run/"heartbeat.json")
        departing = (self.run/"stop").exists()
        now = self.clock()
        reason = "visit_status_unavailable"
        if state is not None:
            if type(state.get("schema_version")) is not int or state["schema_version"] != 1:
                reason = "unsupported_visit_status"
            elif state.get("status") != "running":
                reason = "visit_not_running"
            elif state.get("declared_presence") is not True:
                reason = "driver_presence_not_declared"
            elif not _fresh(state.get("updated_at"), now):
                reason = "visit_status_stale"
            elif (heartbeat is None or type(heartbeat.get("schema_version")) is not int
                  or heartbeat["schema_version"] != 1
                  or not _fresh(heartbeat.get("at"), now)):
                reason = "coordinator_heartbeat_stale"
            elif departing:
                reason = "departure_already_requested"
            else:
                reason = None
        radio = state.get("radio") if state else None
        reported_mute = radio.get("muted") if isinstance(radio, dict) else None
        return dict(controllable=reason is None, reason=reason,
                    mute_available=True,
                    visit_status=state.get("status") if state else "unknown",
                    reported_mute=reported_mute if reason is None
                    and type(reported_mute) is bool else None,
                    capture_ready=reason is None and published_capture_ready(state, now=now))

    def send(self, action, **fields):
        if action not in ("mute", "stop", "reason"):
            raise ValueError("unsupported_control")
        if action == "mute" and (set(fields) != {"muted"} or type(fields["muted"]) is not bool):
            raise ValueError("mute_requires_boolean")
        if action == "reason" and (set(fields) != {"reason"} or fields["reason"] not in REASONS):
            raise ValueError("invalid_reason")
        if action == "stop" and fields:
            raise ValueError("invalid_departure_fields")
        command_id = uuid4().hex
        audit = self.run/"controls"
        audit.mkdir(exist_ok=True)
        at, elapsed = self.clock(), self.elapsed()
        if not _fresh(at, at) or not _fresh(elapsed, elapsed) or elapsed < 0:
            raise ValueError("invalid_control_clock")
        intent = dict(schema_version=1, command_id=command_id, action=action,
                      at=at, elapsed=elapsed, **fields)
        write_status(audit/f"{command_id}.intent.json", intent)
        lock = None
        result = dict(schema_version=1, command_id=command_id, action=action,
                      state="refused", reason=None, at=at)
        try:
            lock = SourceLock(self.run/"request.lock")
            current = self.status()
            if not current["controllable"] and not (action == "mute" and fields["muted"]):
                result["reason"] = current["reason"]
            elif action == "stop":
                # The intent timestamp precedes audit I/O/locking. A clock
                # observation may have committed since then; choose the accepted
                # boundary under the same lock and retain both times in the audit.
                boundary_at, boundary_elapsed = self.clock(), self.elapsed()
                if (not _fresh(boundary_at, boundary_at)
                        or not _fresh(boundary_elapsed, boundary_elapsed)
                        or boundary_elapsed < elapsed):
                    result["reason"] = "invalid_control_clock"
                else:
                    write_status(self.run/"stop", dict(
                        at=boundary_at, elapsed=boundary_elapsed,
                        declaration="driver_left", command_id=command_id))
                    result.update(state="requested", boundary_at=boundary_at,
                                  boundary_elapsed=boundary_elapsed)
            elif action == "mute":
                # Priority state only: replaying a queued older Unmute would be unsafe.
                previous = _read(self.run/"mute.json")
                watermark = 0.0
                if previous is not None:
                    prior_at = previous.get("at")
                    prior_watermark = previous.get("intent_watermark", prior_at)
                    if (type(previous.get("schema_version")) is not int
                            or previous["schema_version"] != 1
                            or type(previous.get("muted")) is not bool
                            or not _fresh(prior_at, prior_at)
                            or not _fresh(prior_watermark, prior_watermark)
                            or prior_watermark < prior_at):
                        previous = None
                    else:
                        watermark = prior_watermark
                unreadable = previous is None and (self.run/"mute.json").exists()
                if not fields["muted"] and unreadable:
                    result["reason"] = "mute_state_unreadable"
                elif not fields["muted"] and at <= watermark:
                    result["reason"] = "obsolete_unmute_intent"
                else:
                    # A delayed inhibit remains safe, but cannot lower the floor
                    # and permit an older Unmute that the host never observed.
                    write_status(self.run/"mute.json", dict(
                        schema_version=1, command_id=command_id, muted=fields["muted"], at=at,
                        intent_watermark=max(watermark, at)))
                    result["state"] = "requested"
            else:
                write_status(self.run/"requests"/f"{command_id}.json", intent)
                result["state"] = "requested"
        except RuntimeError:
            result["reason"] = "control_writer_busy"
        except OSError as exc:
            result.update(state="write_failed", reason=type(exc).__name__)
        finally:
            if lock:
                lock.close()
        write_status(audit/f"{command_id}.result.json", result)
        return result

    def receipt(self, command_id):
        if (not isinstance(command_id, str) or len(command_id) != 32
                or any(c not in "0123456789abcdef" for c in command_id)):
            raise ValueError("invalid_command_id")
        value = _read(self.run/"receipts"/f"{command_id}.json")
        if value is None or type(value.get("accepted")) is not bool:
            return None
        return dict(accepted=value["accepted"], reason=value.get("reason"),
                    applied=value.get("applied"), muted=value.get("muted"))


def show_window(controller):
    """Lazy Tk import: importing or testing the controller never opens a window."""
    import tkinter as tk
    from tkinter import ttk

    try:
        window = tk.Tk()
    except tk.TclError:
        raise RuntimeError("tk_display_unavailable") from None
    window.title("Race Chief visit controls")
    frame = ttk.Frame(window, padding=16)
    frame.grid(sticky="nsew")
    status = tk.StringVar(value="Checking visit…")
    response = tk.StringVar(value="Controls send requests; acknowledgment is shown separately.")
    reason = tk.StringVar(value="intentional_rest")
    pending = None

    def send(action, **fields):
        nonlocal pending
        try:
            result = controller.send(action, **fields)
            pending = result["command_id"] if result["state"] == "requested" else None
            response.set(f"{action.title()}: {result['state']}"
                         + (f" ({result['reason']})" if result["reason"] else ""))
        except (OSError, ValueError, RuntimeError) as exc:
            response.set(f"Request failed: {type(exc).__name__}")

    ttk.Label(frame, textvariable=status, wraplength=470).grid(columnspan=3, sticky="w", pady=6)
    buttons = []
    for column, (label, action, values) in enumerate((
        ("Mute", "mute", {"muted": True}), ("Unmute", "mute", {"muted": False}),
        ("End visit", "stop", {}),
    )):
        button = ttk.Button(frame, text=label, padding=12,
                            command=lambda a=action, v=values: send(a, **v))
        button.grid(row=1, column=column, padx=4, pady=8, sticky="ew")
        buttons.append(button)
    ttk.Label(frame, text="Reason for non-driving time:").grid(row=2, columnspan=3, sticky="w")
    choices = ttk.Combobox(frame, textvariable=reason, values=REASONS, state="readonly", width=24)
    choices.grid(row=3, column=0, columnspan=2, sticky="ew", pady=8)
    apply = ttk.Button(frame, text="Apply reason",
                       command=lambda: send("reason", reason=reason.get()))
    apply.grid(row=3, column=2, padx=4)
    buttons.append(apply)
    ttk.Label(frame, textvariable=response, wraplength=470).grid(
        row=4, columnspan=3, sticky="w", pady=8)
    ttk.Label(frame, text="Closing this window does not end your visit.").grid(
        row=5, columnspan=3, sticky="w")

    def refresh():
        current = controller.status()
        mute_text = {True: "on", False: "off", None: "unknown"}[current["reported_mute"]]
        status.set(f"Visit: {current['visit_status']} · " + (
            "Controls available" if current["controllable"] else str(current["reason"]))
                   + f"\nCoordinator-reported mute: {mute_text}")
        for index, button in enumerate(buttons):
            enabled = index == 0 or current["controllable"]
            button.configure(state="normal" if enabled else "disabled")
        if pending:
            receipt = controller.receipt(pending)
            if receipt is not None:
                response.set("Coordinator acknowledged request." if receipt["accepted"] else
                             f"Coordinator refused request: {receipt['reason']}")
        window.after(200, refresh)

    refresh()
    # No topmost, focus forcing, hotkey registration or later window raising.
    window.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visit", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        controller = VisitControls(args.visit)
        show_window(controller)
    except ImportError:
        parser.error("Tkinter is unavailable in this Python installation")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(f"controls unavailable: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
