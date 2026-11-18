"""Local radio prototype: dry speech, real DSP, and evidence-gated dispatch.

Nothing plays on import or render. Dispatcher timestamps are UTC epoch seconds.
Playback completion is an OS API result, never evidence that a driver heard it.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from array import array
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from simcoach.live.output_owner import WindowsOutputClaim


def _finite(value: float) -> bool:
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_new(path: Path, value: dict) -> None:
    """A new artifact only; preserve a failed partial write for diagnosis."""
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    if path.exists():
        raise FileExistsError(path)
    partial.rename(path)


def read_pcm(path: Path) -> tuple[int, array]:
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        frames = source.getnframes()
        if (source.getnchannels() != 1 or source.getsampwidth() != 2
                or source.getcomptype() != "NONE" or not 8000 <= rate <= 48000
                or not 0 < frames <= rate * 30):
            raise ValueError("Expected 0-30 seconds of 8-48 kHz mono PCM16 audio")
        raw = source.readframes(frames)
        if len(raw) != frames * 2:
            raise ValueError("Truncated PCM audio")
    samples = array("h", raw)
    if sys.byteorder != "little":
        samples.byteswap()
    return rate, samples


def process_radio(source: Path, destination: Path, *, intensity: float = 0.7) -> dict:
    """Band-limit then soft-compress/saturate speech, retaining duration and rate."""
    if not _finite(intensity) or not 0 <= intensity <= 1:
        raise ValueError("intensity must be finite and between zero and one")
    if destination.exists():
        raise FileExistsError(destination)
    rate, samples = read_pcm(source)
    high = math.exp(-2 * math.pi * 300 / rate)
    low = 1 - math.exp(-2 * math.pi * 3400 / rate)
    previous = high_state = low_one = low_two = 0.0
    result = array("h")
    attack, release = max(1, int(rate * 0.008)), max(1, int(rate * 0.020))
    for index, value in enumerate(samples):
        dry = value / 32768
        high_state = high * (high_state + dry - previous)
        previous = dry
        low_one += low * (high_state - low_one)
        low_two += low * (low_one - low_two)
        wet = 0.65 * math.tanh(low_two * 2.0)
        envelope = min(1.0, index / attack, (len(samples) - 1 - index) / release)
        mixed = ((1 - intensity) * dry + intensity * wet) * envelope
        result.append(round(max(-0.85, min(0.85, mixed)) * 32767))
    if sys.byteorder != "little":
        result.byteswap()
    partial = destination.with_suffix(destination.suffix + ".partial")
    with partial.open("xb") as output:
        with wave.open(output, "wb") as rendered:
            rendered.setnchannels(1)
            rendered.setsampwidth(2)
            rendered.setframerate(rate)
            rendered.writeframes(result.tobytes())
        output.flush()
        os.fsync(output.fileno())
    partial.rename(destination)
    return {"dsp_version": 1, "intensity": intensity, "sample_rate_hz": rate,
            "frames": len(samples), "duration_s": len(samples) / rate,
            "dry_sha256": _hash(source), "radio_sha256": _hash(destination)}


_SPEECH_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $installed = @($voice.GetInstalledVoices() | Where-Object { $_.Enabled })
    if ($installed.Count -eq 0) { throw 'No enabled local speech voice' }
    $voice.SelectVoice($installed[0].VoiceInfo.Name)
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
        22050, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    $voice.SetOutputToWaveFile($env:ORC_RADIO_WAV, $format)
    $voice.Speak($env:ORC_RADIO_TEXT)
    @{voice = $voice.Voice.Name; engine = 'System.Speech'} | ConvertTo-Json -Compress
} finally { $voice.Dispose() }
"""


def render_speech(text: str, output_dir: Path, *, intensity: float = 0.7) -> dict:
    """Render installed Windows speech to files; never to an audio device."""
    if os.name != "nt":
        raise RuntimeError("Dry speech rendering requires Windows System.Speech")
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        raise ValueError("Speech must contain 1-500 characters")
    if not _finite(intensity) or not 0 <= intensity <= 1:
        raise ValueError("Invalid radio intensity")
    output_dir.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    partial = output_dir / "dry.wav.partial"
    environment = dict(os.environ, ORC_RADIO_TEXT=text, ORC_RADIO_WAV=str(partial.resolve()))
    # User text and paths are data in the child environment, never shell source.
    executable = shutil.which("pwsh") or str(
        Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    command = [str(executable), "-NoProfile", "-NonInteractive", "-Command", _SPEECH_SCRIPT]
    try:
        run = subprocess.run(command, env=environment, capture_output=True, text=True,
                             encoding="utf-8",
                             timeout=30, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        (output_dir / "synthesis.stdout.txt").write_text(run.stdout, encoding="utf-8")
        (output_dir / "synthesis.stderr.txt").write_text(run.stderr, encoding="utf-8")
        if run.returncode:
            raise RuntimeError(f"Dry speech synthesis failed with exit {run.returncode}")
        voice = json.loads(run.stdout)
        read_pcm(partial)
        dry = output_dir / "dry.wav"
        partial.rename(dry)
        synthesis_s = time.perf_counter() - started
        dsp = process_radio(dry, output_dir / "radio.wav", intensity=intensity)
        result = {"schema_version": 1, "status": "generated", "text": text, **voice, **dsp,
                  "powershell_executable": executable,
                  "synthesis_s": synthesis_s, "render_total_s": time.perf_counter() - started,
                  "audio_output_attempted": False, "audible_onset_s": None}
        _json_new(output_dir / "render.json", result)
        return result
    except Exception as error:
        _json_new(output_dir / "failed.json", {"schema_version": 1, "status": "failed",
                  "error_type": type(error).__name__, "error": str(error),
                  "audio_output_attempted": False})
        raise


@dataclass(frozen=True)
class Transmission:
    transmission_id: str
    dedup_key: str
    evidence_id: str
    wav_path: Path
    priority: int
    created_at: float
    expires_at: float
    optional: bool = False

    def _validate_fields(self) -> None:
        for value in (self.transmission_id, self.dedup_key, self.evidence_id):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError("Transmission identity must contain 1-256 characters")
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise ValueError("Priority must be an integer from 0 to 100; higher goes first")
        if not all(_finite(v) for v in (self.created_at, self.expires_at)):
            raise ValueError("Transmission times must be finite UTC epoch seconds")
        if self.created_at < 0 or self.expires_at <= self.created_at:
            raise ValueError("Invalid transmission lifetime")
        if type(self.optional) is not bool:
            raise ValueError("optional must be boolean")

    def spec(self) -> dict:
        self._validate_fields()
        data = asdict(self)
        data["wav_path"] = str(Path(self.wav_path).resolve())
        read_pcm(Path(self.wav_path))
        data["wav_sha256"] = _hash(Path(self.wav_path))
        return data


class Transport(Protocol):
    def start(self, path: Path, *, can_start: Callable[[], bool]) -> bool: ...
    def poll(self) -> str: ...  # playing, played or failed
    def stop(self) -> None: ...  # must confirm stop or raise; never overlap on failure


class _WaveFormat(ctypes.Structure):
    _fields_ = [("tag", ctypes.c_uint16), ("channels", ctypes.c_uint16),
                ("rate", ctypes.c_uint32), ("bytes_per_second", ctypes.c_uint32),
                ("align", ctypes.c_uint16), ("bits", ctypes.c_uint16),
                ("extra", ctypes.c_uint16)]


class _WaveHeader(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("length", ctypes.c_uint32),
                ("recorded", ctypes.c_uint32), ("user", ctypes.c_size_t),
                ("flags", ctypes.c_uint32), ("loops", ctypes.c_uint32),
                ("next", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


class _WaveOutputCaps(ctypes.Structure):
    _fields_ = [("manufacturer_id", ctypes.c_uint16), ("product_id", ctypes.c_uint16),
                ("driver_version", ctypes.c_uint32), ("name", ctypes.c_wchar * 32),
                ("formats", ctypes.c_uint32), ("channels", ctypes.c_uint16),
                ("reserved", ctypes.c_uint16), ("support", ctypes.c_uint32)]


@dataclass(frozen=True)
class OutputSelection:
    """Expected name/capability identity, not a guaranteed physical endpoint ID."""

    name: str
    fingerprint: str

    def __post_init__(self):
        if (not isinstance(self.name, str) or not self.name.strip()
                or len(self.name) > 256 or "\0" in self.name):
            raise ValueError("Invalid expected audio output name")
        if (not isinstance(self.fingerprint, str) or len(self.fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in self.fingerprint)):
            raise ValueError("Invalid expected audio output fingerprint")

    def to_dict(self) -> dict:
        return dict(schema_version=1, name=self.name, fingerprint=self.fingerprint)

    @classmethod
    def from_dict(cls, value: dict) -> OutputSelection:
        if (not isinstance(value, dict) or set(value) != {"schema_version", "name", "fingerprint"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Unsupported audio output selection")
        return cls(value["name"], value["fingerprint"])


@dataclass(frozen=True)
class OutputDevice:
    index: int
    name: str
    manufacturer_id: int
    product_id: int
    driver_version: int
    formats: int
    channels: int
    support: int

    @property
    def fingerprint(self) -> str:
        fields = asdict(self)
        del fields["index"]
        fields.update(schema_version=1, backend="winmm_caps")
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode("utf-8")).hexdigest()

    def selection(self) -> OutputSelection:
        return OutputSelection(self.name, self.fingerprint)

    def to_dict(self) -> dict:
        return dict(**asdict(self), fingerprint=self.fingerprint,
                    selection=self.selection().to_dict())


def _bind_inventory(dll) -> None:
    dll.waveOutGetNumDevs.argtypes = []
    dll.waveOutGetNumDevs.restype = ctypes.c_uint32
    dll.waveOutGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(_WaveOutputCaps),
                                      ctypes.c_uint32]
    dll.waveOutGetDevCapsW.restype = ctypes.c_uint32


def _output_caps(dll, identifier: int, *, index: int) -> OutputDevice:
    caps = _WaveOutputCaps()
    code = dll.waveOutGetDevCapsW(identifier, ctypes.byref(caps), ctypes.sizeof(caps))
    if code:
        raise RuntimeError(f"waveOutGetDevCapsW failed with WinMM code {code}")
    if not caps.name.strip() or not 0 < caps.channels <= 64:
        raise RuntimeError("Invalid audio output capabilities")
    return OutputDevice(index, caps.name, caps.manufacturer_id, caps.product_id,
                        caps.driver_version, caps.formats, caps.channels, caps.support)


def _output_devices(dll) -> list[OutputDevice]:
    count = dll.waveOutGetNumDevs()
    if type(count) is not int or not 0 <= count <= 256:
        raise RuntimeError("Invalid audio output device count")
    devices = [_output_caps(dll, index, index=index) for index in range(count)]
    if dll.waveOutGetNumDevs() != count:
        raise RuntimeError("Audio outputs changed during inventory")
    return devices


def list_output_devices() -> list[OutputDevice]:
    """Read native output capabilities without opening, playing or changing a device."""
    if os.name != "nt":
        raise RuntimeError("Windows audio inventory required")
    dll = ctypes.WinDLL("winmm")
    _bind_inventory(dll)
    return _output_devices(dll)


class WindowsWaveTransport:
    """Explicit audio opt-in with optional expected output selection.

    The legacy WinMM transport avoids a worker process surviving the dispatcher.
    A supplied selection never falls back to default. A missing selection retains
    the explicit legacy default-device mode; visit hosts should require selection.
    WASAPI endpoint identity and real hardware timing remain follow-up work.
    """

    def __init__(self, *, enable_audio: bool = False, selection: OutputSelection | None = None):
        if enable_audio is not True:
            raise ValueError("Audio output requires explicit enable_audio=True")
        if selection is not None and not isinstance(selection, OutputSelection):
            raise ValueError("Audio selection must be an OutputSelection")
        if os.name != "nt":
            raise RuntimeError("Windows audio output required")
        self._dll = ctypes.WinDLL("winmm")
        _bind_inventory(self._dll)
        self._dll.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
                                         ctypes.POINTER(_WaveFormat), ctypes.c_size_t,
                                         ctypes.c_size_t, ctypes.c_uint32]
        for name in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader"):
            getattr(self._dll, name).argtypes = [ctypes.c_void_p, ctypes.POINTER(_WaveHeader),
                                               ctypes.c_uint32]
        for name in ("waveOutReset", "waveOutClose"):
            getattr(self._dll, name).argtypes = [ctypes.c_void_p]
        self._handle = ctypes.c_void_p()
        self._header = _WaveHeader()
        self._buffer = None
        self._prepared = False
        self.selection = selection
        self.selected_device: OutputDevice | None = None
        self._claim = WindowsOutputClaim()
        self._closed = False

    def _resolve_selection(self) -> OutputDevice:
        expected = self.selection
        matches = [device for device in _output_devices(self._dll)
                   if device.selection() == expected]
        if len(matches) != 1:
            raise RuntimeError("Selected audio output is missing or ambiguous")
        device = matches[0]
        # IDs are current positions, not durable identity. Recheck the current
        # position immediately before opening rather than trusting a saved index.
        if _output_caps(self._dll, device.index, index=device.index).selection() != expected:
            raise RuntimeError("Selected audio output changed before open")
        return device

    def _verify_opened_selection(self) -> None:
        if self.selection is not None:
            self._resolve_selection()  # Also refuse a newly ambiguous capability identity.
            opened = _output_caps(self._dll, self._handle.value,
                                  index=self.selected_device.index)
            if opened.selection() != self.selection:
                raise RuntimeError("Opened audio output does not match selection")

    def _call(self, name: str, *args) -> None:
        code = getattr(self._dll, name)(*args)
        if code:
            raise RuntimeError(f"{name} failed with WinMM code {code}")

    def prepare_owner(self) -> None:
        """Reserve ORC output before readiness, without opening an audio device."""
        if self._closed:
            raise RuntimeError("Audio transport is closed")
        if self._claim.state == "unclaimed":
            self._claim.acquire()
        if self._claim.state != "held":
            raise RuntimeError("Audio output ownership unavailable")

    def start(self, path: Path, *, can_start: Callable[[], bool]) -> bool:
        if self._closed:
            raise RuntimeError("Audio transport is closed")
        if self._handle.value:
            raise RuntimeError("Audio transport is busy")
        self.prepare_owner()
        rate, samples = read_pcm(path)
        if sys.byteorder != "little":
            samples.byteswap()
        raw = samples.tobytes()
        self._buffer = ctypes.create_string_buffer(raw)
        self._header = _WaveHeader(ctypes.cast(self._buffer, ctypes.c_void_p), len(raw))
        fmt = _WaveFormat(1, 1, rate, rate * 2, 2, 16, 0)
        try:
            self.selected_device = self._resolve_selection() if self.selection else None
            identifier = self.selected_device.index if self.selected_device else 0xFFFFFFFF
            self._call("waveOutOpen", ctypes.byref(self._handle), identifier,
                       ctypes.byref(fmt), 0, 0, 0)
            self._verify_opened_selection()
            self._call("waveOutPrepareHeader", self._handle, ctypes.byref(self._header),
                       ctypes.sizeof(self._header))
            self._prepared = True
            self._verify_opened_selection()
            if can_start() is not True:
                self.stop()
                return False
            self._call("waveOutWrite", self._handle, ctypes.byref(self._header),
                       ctypes.sizeof(self._header))
            return True
        except Exception:
            self.stop()
            raise

    def poll(self) -> str:
        if not self._handle.value:
            return "played"
        self._verify_opened_selection()
        if self._header.flags & 1:  # WHDR_DONE: driver finished with this buffer.
            self._release()
            return "played"
        return "playing"

    def _release(self) -> None:
        if self._prepared:
            self._call("waveOutUnprepareHeader", self._handle, ctypes.byref(self._header),
                       ctypes.sizeof(self._header))
            self._prepared = False
        self._call("waveOutClose", self._handle)
        self._handle = ctypes.c_void_p()
        self._buffer = None

    def stop(self) -> None:
        if self._handle.value:
            self._call("waveOutReset", self._handle)
            self._release()

    @property
    def ownership(self) -> dict:
        return self._claim.snapshot()

    def close(self) -> None:
        # Stop, native buffer release and device close must all succeed before
        # another process may claim output. Idle/muted periods retain this claim.
        self.stop()
        self._claim.close()
        self._closed = True


class Dispatcher:
    """Single-threaded owner with locked journal, persistent dedup, and no replay.

    Call tick regularly. Clock/evidence errors stop the transport and propagate;
    caller must surface degraded audio. A stopped/failed message is never retried.
    Use one shared journal for the visit's radio owner, never one per specialist.
    """

    def __init__(self, journal_path: Path, transport: Transport, *,
                 clock: Callable[[], float] = time.time,
                 evidence_valid: Callable[[Transmission], bool],
                 monotonic: Callable[[], float] = time.perf_counter):
        self.transport, self.clock, self.evidence_valid = transport, clock, evidence_valid
        self.monotonic = monotonic
        self.timing_epoch = uuid4().hex
        self._transport_call: tuple[str, dict] | None = None
        self.queue: dict[str, Transmission] = {}
        self.active: Transmission | None = None
        self.muted = False
        self.closed = False
        self.faulted = False
        self.specs: dict[str, dict] = {}
        self.states: dict[str, str] = {}
        self.keys: set[str] = set()
        self._last_now = 0.0
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = journal_path.with_suffix(journal_path.suffix + ".lock").open("a+b")
        if os.fstat(self._lock.fileno()).st_size == 0:
            self._lock.write(b"0")
            self._lock.flush()
        self._lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise RuntimeError("Another dispatcher owns this journal") from None
        try:
            self._journal = journal_path.open("a+", encoding="utf-8")
            self._journal.seek(0)
            for line in self._journal:
                row = json.loads(line)
                if (type(row.get("schema_version")) is not int
                        or row["schema_version"] != 1 or not _finite(row.get("at"))):
                    raise ValueError("Unsupported radio journal")
                identity = row["transmission_id"]
                if not isinstance(identity, str) or not identity:
                    raise ValueError("Invalid journal identity")
                if not isinstance(row.get("reason"), str) or not row["reason"]:
                    raise ValueError("Radio event requires a reason")
                self._transition(identity, row["state"])
                if row["state"] == "generated":
                    if identity in self.specs:
                        raise ValueError("Repeated generated identity in journal")
                    spec = dict(row["spec"])
                    digest = spec.pop("wav_sha256")
                    if (not isinstance(digest, str) or len(digest) != 64
                            or any(c not in "0123456789abcdef" for c in digest)):
                        raise ValueError("Invalid audio hash")
                    message = Transmission(**spec)
                    message._validate_fields()
                    if (not isinstance(spec["wav_path"], str)
                            or not Path(spec["wav_path"]).is_absolute()):
                        raise ValueError("Journal WAV locator must be absolute")
                    if message.dedup_key in self.keys:
                        raise ValueError("Repeated radio event deduplication key")
                    if message.transmission_id != identity:
                        raise ValueError("Journal identity does not match specification")
                    self.specs[identity] = row["spec"]
                    self.keys.add(row["spec"]["dedup_key"])
                elif identity not in self.specs:
                    raise ValueError("Radio event has no generated specification")
                self.states[identity] = row["state"]
            self._journal.seek(0, 2)
            for identity, state in list(self.states.items()):
                if state in {"generated", "queued", "playing"}:
                    self._event(identity, "interrupted", "restart_no_replay")
            prepare_owner = getattr(self.transport, "prepare_owner", None)
            if prepare_owner is not None:
                prepare_owner()
        except Exception:
            if hasattr(self, "_journal"):
                self._journal.close()
            self._lock.close()
            # Native reservation never opens an audio device. Preserve cleanup
            # failure rather than reporting successful owner release.
            close_transport = getattr(self.transport, "close", None)
            if close_transport is not None:
                close_transport()
            raise

    def _now(self) -> float:
        now = self.clock()
        if not _finite(now) or now < self._last_now:
            self.faulted = True
            self.transport.stop()
            raise ValueError("Invalid or regressing radio clock")
        self._last_now = now
        return now

    def _event(self, identity: str, state: str, reason: str, **extra) -> None:
        self._transition(identity, state)
        row = {"schema_version": 1, **self._timing_point(at=self._now()),
               "transmission_id": identity,
               "state": state, "reason": reason, **extra}
        if self._transport_call is not None and self._transport_call[0] == identity:
            row["transport_start"] = self._transport_call[1]
            self._transport_call = None
        try:
            self._journal.write(json.dumps(row, allow_nan=False) + "\n")
            self._journal.flush()
            os.fsync(self._journal.fileno())
        except Exception:
            self.faulted = True
            self.transport.stop()
            raise
        self.states[identity] = state

    def _timing_point(self, *, at=None) -> dict:
        """Diagnostic sampling only. Failure here cannot change audio decisions."""
        try:
            at = self.clock() if at is None else at
            elapsed = self.monotonic()
            return dict(at=at if _finite(at) else None,
                        monotonic_s=elapsed if _finite(elapsed) else None,
                        timing_epoch=self.timing_epoch)
        except Exception:
            return dict(at=at if _finite(at) else None, monotonic_s=None,
                        timing_epoch=self.timing_epoch)

    def _transition(self, identity: str, state: str) -> None:
        allowed = {
            None: {"generated"},
            "generated": {"queued", "suppressed", "interrupted"},
            "queued": {"playing", "expired", "suppressed", "interrupted", "failed"},
            "playing": {"played", "failed", "interrupted", "expired"},
            "interrupted": {"failed"},
            "failed": {"interrupted"},
            "played": {"acknowledged"},
        }
        if state not in allowed.get(self.states.get(identity), set()):
            raise ValueError("Invalid radio journal state transition")

    def _usable(self) -> None:
        if self.closed or self.faulted:
            raise RuntimeError("Radio dispatcher is closed or faulted")

    def submit(self, message: Transmission, *, source_at: float | None = None) -> bool:
        self._usable()
        spec = message.spec()
        identity = message.transmission_id
        if identity in self.specs:
            if self.specs[identity] != spec:
                raise ValueError("Transmission ID reused with different content")
            return False
        if message.dedup_key in self.keys:
            return False
        if message.created_at > self._now():
            raise ValueError("Transmission creation is in the future")
        # Optional producer time is diagnostic. Omit an invalid or unknown time.
        timing = ({"source_at": source_at}
                  if _finite(source_at) and 0 <= source_at <= message.created_at else {})
        self._event(identity, "generated", "audio_artifact_available", spec=spec, **timing)
        self.specs[identity] = spec
        self.keys.add(message.dedup_key)
        if self.muted:
            self._event(identity, "suppressed", "submitted_while_muted")
            return False
        self._event(identity, "queued", "submitted")
        self.queue[identity] = message
        return True

    def _stop(self, reason: str) -> None:
        if self.active:
            message = self.active
            try:
                self.transport.stop()
            except Exception:
                self.faulted = True
                self._event(message.transmission_id, "failed", "stop_unconfirmed")
                raise
            self.active = None
            self._event(message.transmission_id, "interrupted", reason)

    def set_muted(self, muted: bool) -> None:
        self._usable()
        if type(muted) is not bool:
            raise ValueError("Mute must be boolean")
        self.muted = muted
        if muted:
            self._stop("muted")
            for identity in list(self.queue):
                self._event(identity, "suppressed", "muted")
                del self.queue[identity]

    def _valid(self, message: Transmission) -> bool:
        try:
            return self.evidence_valid(message) is True
        except Exception:
            self._stop("evidence_check_failed")
            self.faulted = True
            raise

    def tick(self, *, allow_optional: bool = True) -> None:
        self._usable()
        now = self._now()
        for identity, message in list(self.queue.items()):
            state = None
            if now >= message.expires_at:
                state, reason = "expired", "deadline"
            elif self.muted:
                state, reason = "suppressed", "muted"
            elif not self._valid(message):
                state, reason = "suppressed", "evidence_no_longer_valid"
            if state:
                self._event(identity, state, reason)
                del self.queue[identity]
        if self.active:
            current = self.active
            try:
                outcome = self.transport.poll()
            except Exception:
                self._stop("transport_poll_failed")
                self.faulted = True
                raise
            if outcome == "failed":
                self._stop("transport_reported_failure")
                self._event(current.transmission_id, "failed", "transport_reported")
            elif outcome == "played":
                self._event(current.transmission_id, outcome, "transport_reported",
                            audible_onset_at=None, driver_heard=None)
                self.active = None
            elif outcome != "playing":
                self._stop("invalid_transport_state")
                self.faulted = True
                raise ValueError("Invalid transport state")
            elif now >= current.expires_at or not self._valid(current):
                self._stop("expired_or_evidence_invalid")
            elif current.optional and not allow_optional:
                self._stop("workload_changed")
        ready = [message for message in self.queue.values()
                 if allow_optional or not message.optional]
        if self.muted or not ready:
            return
        selected = min(ready, key=lambda m: (-m.priority, m.created_at, m.transmission_id))
        if self.active:
            if selected.priority <= self.active.priority:
                return
            self._stop("higher_priority")
        # Validation and artifact integrity are rechecked immediately before output.
        if self._now() >= selected.expires_at or not self._valid(selected):
            self._event(selected.transmission_id, "expired", "recheck_before_output")
            del self.queue[selected.transmission_id]
            return
        try:
            unchanged = _hash(Path(selected.wav_path)) == self.specs[
                selected.transmission_id]["wav_sha256"]
        except OSError:
            unchanged = False
        if not unchanged:
            self._event(selected.transmission_id, "failed", "audio_artifact_changed_or_unreadable")
            del self.queue[selected.transmission_id]
            return
        self._event(selected.transmission_id, "playing", "output_requested",
                    queued_delay_s=self._now() - selected.created_at, audible_onset_at=None)
        del self.queue[selected.transmission_id]
        self.active = selected
        # Journaling may block on storage; a fresh check follows the durable intent.
        if not self._valid(selected) or self._now() >= selected.expires_at:
            self.active = None
            self._event(selected.transmission_id, "expired", "expired_before_output")
            return
        try:
            call = dict(requested=self._timing_point(), confirmed=None)
            self._transport_call = (selected.transmission_id, call)
            started = self.transport.start(
                Path(selected.wav_path),
                can_start=lambda: (not self.muted and self._valid(selected)
                                   and self._now() < selected.expires_at),
            )
            if started is False:
                self.active = None
                self._event(selected.transmission_id, "expired", "transport_start_guard")
            elif started is not True:
                raise ValueError("Transport must confirm started or suppressed output")
            else:
                call["confirmed"] = self._timing_point()
        except Exception:
            self._stop("transport_start_failed")
            self._event(selected.transmission_id, "failed", "transport_start_failed")

    def acknowledge(self, transmission_id: str) -> None:
        self._usable()
        if self.states.get(transmission_id) != "played":
            raise ValueError("Only a completed transmission can be acknowledged")
        self._event(transmission_id, "acknowledged", "explicit_driver_confirmation")

    def close(self) -> None:
        if self.closed:
            return
        # Retain ownership when stop is unconfirmed. A successor must not overlap.
        self._stop("dispatcher_closed")
        for identity in list(self.queue):
            self._event(identity, "interrupted", "dispatcher_closed")
            del self.queue[identity]
        close_transport = getattr(self.transport, "close", None)
        if close_transport is not None:
            close_transport()
        self.closed = True
        self._journal.close()
        self._lock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render speech or list outputs; never plays audio")
    parser.add_argument("--list-outputs", action="store_true", help="Read local Windows inventory")
    parser.add_argument("--text")
    parser.add_argument("--out", type=Path, help="New private output directory")
    parser.add_argument("--intensity", type=float, default=0.7)
    args = parser.parse_args(argv)
    if args.list_outputs and (args.text is not None or args.out is not None):
        parser.error("--list-outputs cannot be combined with render arguments")
    if not args.list_outputs and (args.text is None or args.out is None):
        parser.error("rendering requires --text and --out")
    try:
        if args.list_outputs:
            devices = list_output_devices()
            print(json.dumps(dict(
                schema_version=1, devices=[device.to_dict() for device in devices],
                inventory_health="available" if devices else "unknown",
                reason=None if devices else "no_devices_or_inventory_error",
                audio_output_attempted=False, route_verified=False,
            )))
            return 0
        print(json.dumps(render_speech(args.text, args.out, intensity=args.intensity)))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Radio render failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
