#!/usr/bin/env python3
"""Observe live AMS2 control response on Windows and emit a single JSON result."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from contextlib import suppress
from threading import Thread

from simcoach.adapters.ams2 import STORED_PARTICIPANTS_MAX
from simcoach.live.rig_controller import (
    ControllerError,
    PhysicalController,
    discover_controllers,
    open_controller,
)
from simcoach.live.rig_inputs import ams2_reader, observe, result_template


def controller_listing() -> dict:
    result = {
        "schema_version": 1, "source": "winmm", "status": "unavailable",
        "reason": "windows_required", "controllers": [],
    }
    if sys.platform != "win32":
        return result
    try:
        controllers = discover_controllers()
    except (ControllerError, OSError):
        result["reason"] = "controller_discovery_failed"
        return result
    result["controllers"] = [
        {"id": item.id, "name": item.name, "axes": item.axes} for item in controllers
    ]
    if controllers:
        result.update(status="observed", reason="controllers_available")
    else:
        result["reason"] = "no_readable_controllers"
    return result


def _select_controller(controller_id: int | None, name: str | None) -> PhysicalController | None:
    with suppress(ControllerError, OSError, ValueError):
        if controller_id is not None:
            return open_controller(controller_id)
        if name is not None:
            matches = [item for item in discover_controllers() if item.name == name]
            if len(matches) == 1:
                device = open_controller(matches[0].id)
                if device.identity == matches[0]:
                    return device
    return None


def _parked_prompts(*, text_only: bool):
    from winsound import Beep

    count = 0

    def play(notes: int) -> None:
        try:
            for note in range(notes):
                if note:
                    time.sleep(0.08)
                Beep(720, 115)
        except RuntimeError:
            print("Windows could not play the cue. Use the text prompts.", file=sys.stderr)

    def announce(message: str) -> None:
        nonlocal count
        count += 1
        print(message, file=sys.stderr, flush=True)
        if not text_only:
            Thread(target=play, args=(count,), daemon=True).start()

    return announce


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-controllers", action="store_true", help="List readable WinMM slots")
    parser.add_argument("--mode", choices=("passive", "parked"), default="passive")
    parser.add_argument("--duration-s", type=float, help="Observation time after source admission")
    parser.add_argument(
        "--confirm-player-view", action="store_true",
        help="Driver confirms their own live human cockpit with AI/autopilot off throughout",
    )
    parser.add_argument("--participant-index", type=int, help="Optional expected viewed slot")
    parser.add_argument(
        "--confirm-physical-controls", action="store_true",
        help="Driver confirms they are safely clear of traffic and will use the rig controls",
    )
    controller = parser.add_mutually_exclusive_group()
    controller.add_argument("--controller-id", type=int, help="Optional operator-selected slot")
    controller.add_argument("--controller-name", help="Optional exact unique WinMM product label")
    parser.add_argument("--pedal-controller-id", type=int, help="Separate slot for both pedals")
    parser.add_argument("--text-only", action="store_true", help="Disable parked check audio cues")
    args = parser.parse_args(argv)
    if args.list_controllers:
        if (
            args.mode != "passive" or args.duration_s is not None or args.confirm_player_view
            or args.participant_index is not None or args.confirm_physical_controls
            or args.controller_id is not None or args.controller_name is not None
            or args.pedal_controller_id is not None or args.text_only
        ):
            parser.error("--list-controllers must be used by itself")
        print(json.dumps(controller_listing(), allow_nan=False))
        return 0
    parked = args.mode == "parked"
    minimum = 6 if parked else 1
    duration = args.duration_s if args.duration_s is not None else (15 if parked else 10)
    if not math.isfinite(duration) or not minimum <= duration <= 30:
        parser.error(f"--duration-s must be from {minimum} to 30")
    if args.participant_index is not None and not (
        args.confirm_player_view and 0 <= args.participant_index < STORED_PARTICIPANTS_MAX
    ):
        parser.error("--participant-index requires a valid slot and --confirm-player-view")
    if args.confirm_physical_controls and not parked:
        parser.error("--confirm-physical-controls requires parked mode")
    if args.text_only and not parked:
        parser.error("--text-only requires parked mode")
    if args.controller_id is not None and not (parked and 0 <= args.controller_id <= 15):
        parser.error("--controller-id requires parked mode and a WinMM slot from 0 to 15")
    if args.controller_name is not None and not (parked and args.controller_name.strip()):
        parser.error("--controller-name requires parked mode and a nonempty product label")
    if args.pedal_controller_id is not None and not (
        parked and 0 <= args.pedal_controller_id <= 15
    ):
        parser.error("--pedal-controller-id requires parked mode and a WinMM slot from 0 to 15")

    result = result_template(args.mode)
    if parked and not (args.confirm_player_view and args.confirm_physical_controls):
        result["reason"] = "driver_confirmation_required"
    elif sys.platform != "win32":
        result["reason"] = "windows_required"
    else:
        device = _select_controller(args.controller_id, args.controller_name)
        physical_read = device.sample if device is not None else None
        pedal_read = None
        if args.pedal_controller_id is not None:
            if device is not None and device.identity.id == args.pedal_controller_id:
                pedal_read = physical_read
            else:
                pedal = _select_controller(args.pedal_controller_id, None)
                pedal_read = pedal.sample if pedal is not None else None
        if parked:
            guides = (
                "Follow the text prompts."
                if args.text_only else
                "One beep = wheel, two = throttle, three = brake. "
                "After each cue, gently move the named control, hold briefly, then release."
            )
            print(
                "Switch to your safely parked, neutral AMS2 cockpit now. "
                "Center the wheel and leave the pedals released until each cue. " + guides,
                file=sys.stderr, flush=True,
            )
        options = {"prompt": _parked_prompts(text_only=args.text_only)} if parked else {}
        result = observe(
            ams2_reader(),
            mode=args.mode,
            duration_s=duration,
            player_confirmed=args.confirm_player_view,
            participant_index=args.participant_index,
            physical_confirmed=args.confirm_physical_controls,
            physical_read=physical_read,
            pedal_read=pedal_read,
            separate_pedals=args.pedal_controller_id is not None,
            preparation_s=5 if parked else 0,
            **options,
        )
        if device is not None:
            result["controller_id"] = device.identity.id
        elif args.controller_id is not None:
            result["controller_id"] = args.controller_id
        if args.pedal_controller_id is not None:
            result["pedal_controller_id"] = args.pedal_controller_id
        if device is None and pedal_read is None and (
            args.controller_id is not None or args.controller_name is not None
        ):
            result["controller_status"] = "unavailable"
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
