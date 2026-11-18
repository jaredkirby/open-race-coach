# Local radio prototype

Status: offline prototype. The module can render installed Windows speech into
a dry WAV and a processed radio WAV. Its dispatcher and native output adapter
are implemented; audible headset delivery, intelligibility, device loss,
mute latency and simulator performance still require hardware evidence.
It does not detect a caught slide or select a Coaching Instruction.

## Render without playback

The chief can prepare short operational phrases before driving:

```powershell
uv run python -m simcoach.live.radio --text "Radio check." `
  --out .scratch/radio/check-001
```

The destination must be new. Rendering never opens an audio output device.
It uses local `System.Speech` through PowerShell, preferring `pwsh` on PATH and
otherwise Windows PowerShell. The selected shell and first enabled installed
voice are recorded. Neither a cloud service nor a provider account is required
by this renderer. An unavailable engine is a preserved failure, not a silent
fallback to a remote service. Text is passed as environment data, not shell code.

Microsoft documents directing synthesized speech into a WAV file with a
specified audio format. The prototype requests 22.05 kHz mono PCM16 and validates
the resulting file before processing. [SpeechSynthesizer.SetOutputToWaveFile](https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer.setoutputtowavefile?view=netframework-4.8.1).

The actual DSP applies a 300 Hz high-pass, two 3.4 kHz low-pass stages, smooth
saturation/compression, a short transmission envelope and a bounded peak.
`--intensity` blends the dry and processed signals from zero to one; the envelope
and peak limit also apply at zero. It preserves sample rate and duration. No
random dropouts or masking noise are added. These settings are starting points,
not a validated radio model or an assessment of comfort.

Outputs remain private: `dry.wav`, `radio.wav`, synthesis stdout/stderr and
`render.json`. Failed attempts retain partial files and `failed.json`; a retry
uses another directory. Text and voice details may be sensitive. Generation
time measures file preparation, not event-to-audible latency. Pre-rendering
urgent phrases removes synthesis from their dispatch path.

## One owner and explicit playback

`Dispatcher(journal_path, transport, clock=..., evidence_valid=...)` owns one
radio queue. Each specialist submits through that owner. The journal has an OS
lock, so another dispatcher cannot open the same journal concurrently. Different
journals alone do not arbitrate audio. The native Windows transport now also
reserves `Local\OpenRaceCoach.RadioOutput.v1` before dispatcher initialization
completes. This cooperative guard spans journals and checkouts within one Windows
session. It persists through idle, muted and completed playback until explicit
transport close confirms native stop/device release and closes the claim handle.
Claim failure prevents output and surfaces degraded initialization. It does not
prove the headset works or select which competing host ought to own the visit.

The guard admits only the creator of the named object; any existing object is a
conflict, including a second caller on the same thread. It never waits on mutex
thread ownership or steals a claim after a timeout. This deliberately avoids
treating a terminated thread as permission to overlap a living process's output.
Windows closes process handles at exit; the last closed handle destroys the
object. Access denial or a name collision refuses output. These semantics follow
[CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw).
The `Local` namespace is scoped to the Windows session, not the entire machine;
see [kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces).

The guard does not coordinate older builds, unrelated audio applications or
other Windows sessions. It does not inspect or kill a competing process. A
failed release retains the handle and dispatcher journal ownership for explicit
closure retry. There is no claim expiry or automatic output takeover. Existing
journal deduplication/no-replay still applies after process death. A separate
service prototype now keeps approved supervision and radio outside the coordinator;
see [operations service](operations-service.md). Its ordinary live and audible
failure-handling gates remain unverified. This guard alone is not that service.
The host must still provide one common
dispatcher and call it from one thread.

`Transmission` contains `transmission_id`, `dedup_key`, `evidence_id`, `wav_path`,
`priority`, `created_at`, `expires_at` and `optional`. Times are UTC epoch seconds;
priority is an integer from zero to 100, higher first. IDs identify occurrences,
not a phrase template. Paths locate pre-rendered artifacts. Submission hashes
the file; playback rechecks its hash, expiry and the injected evidence predicate.
Reusing an ID with changed content is an error; repeated IDs or deduplication
keys are not replayed, including after restart.

Call `tick()` regularly. Higher-priority messages interrupt lower-priority
messages; interruption is terminal, never a resumption queue. Use
`tick(allow_optional=False)` to defer optional Live Reactions during workload
and interrupt one already playing. `set_muted(True)` stops current output and
suppresses pending messages. Unmuting does not replay them. Evidence exceptions,
clock regressions, unconfirmed stops and journal failures fault the owner and
must be surfaced by the host as degraded audio. A failed close retains journal
ownership until stop is confirmed; retry closure after resolving the fault.
Submitting while muted immediately consumes and suppresses that occurrence.

The injected transport implements `start(path, can_start=...) -> bool`, `poll()`
and `stop()`. It must call the supplied freshness/expiry guard immediately before
emitting sound, return false only when it emitted nothing, and confirm stop or
raise. The native adapter checks after buffer preparation, before `waveOutWrite`.
This closes a stale-message window during slow file/device preparation; actual
acoustic onset still requires hardware measurement.
Optional `prepare_owner()` reserves cooperative output authority without audio
during dispatcher construction; optional `close()` releases it after confirmed
stop. The fuel adapter forwards both methods. Legacy fake transports can omit
them; they provide no native output-exclusion evidence.

`WindowsWaveTransport(enable_audio=True, selection=expected_output)` explicitly
opts into a selected Windows output. It uses an in-process WinMM buffer, driver
completion flags and reset before release, avoiding an audio subprocess left
running after the chief exits. Creating it without `enable_audio=True` refuses
output. Omitting `selection` retains an explicit legacy default-device mode for
callers that request that behavior; a managed visit should require selection.
A supplied selection never falls back to the default device. WinMM is a legacy
prototype backend; Microsoft recommends WASAPI or Audio Graphs for new audio
work. [Waveform functions](https://learn.microsoft.com/en-us/windows/win32/multimedia/waveform-functions).

The transport treats a finished buffer as API playback completion, and reset
as a requested stop whose return status must succeed. Neither establishes that
the correct headset produced sound. [waveOutWrite](https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/nf-mmeapi-waveoutwrite),
[waveOutReset](https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/nf-mmeapi-waveoutreset).

No command enables playback by default. The host must coordinate the first
listening check, verify the output route, and provide accessible mute before
using this transport during driving. Hardware onset timing remains unknown.

## Selecting the intended headset

The chief can obtain a read-only inventory without opening or playing a device:

```powershell
uv run python -m simcoach.live.radio --list-outputs
```

The JSON includes local device names and capabilities; retain it privately.
`list_output_devices()` provides the same records in Python. Each `OutputDevice`
has `to_dict()` and `selection()`; the latter returns an `OutputSelection` with
`to_dict()`/`from_dict()` for the host's private selection file. Choose an output
based on the actual headset route, not the first index or a plausible name.

WinMM device IDs are inventory positions. The transport re-enumerates and
requires exactly one matching name/capability fingerprint before each open. It
rechecks that position immediately before opening, checks the opened handle's
capabilities before writing audio, and rechecks during polling. Missing,
ambiguous, renamed or capability-changed selections fail; a changed index can
resolve to the same unique fingerprint. Device/open errors never trigger a
default-device retry. The APIs permit capability queries by current ID or open
handle. [waveOutGetDevCapsW](https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/nf-mmeapi-waveoutgetdevcapsw),
[waveOutOpen](https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/nf-mmeapi-waveoutopen).

This fingerprint is **not a guaranteed physical endpoint identity**. WinMM
product names have a fixed-size field and can be truncated; identical devices
may expose identical names/capabilities. Simultaneous ambiguity is refused, but
replacement by an indistinguishable device cannot be detected from these fields.
A real listening check is still required. Inventory does not establish that a
virtual endpoint reaches the worn headset. [WAVEOUTCAPSW](https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/ns-mmeapi-waveoutcapsw).

An empty inventory is unknown: the native count API does not distinguish no
devices from an error. It is not proof that the machine has no audio hardware.
[waveOutGetNumDevs](https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/nf-mmeapi-waveoutgetnumdevs).

Before the next listening check, the chief should save the intended selection,
prepare a short radio-check phrase, establish an accessible mute/stop control,
and verify the driver is ready to listen. Confirm the heard route and comfort
before relying on a fuel call. Recheck after device or configuration changes.
Neither inventory nor fake-device tests replace that check.

## Prototype artifact contracts

See [radio artifact schema](../schema/radio.md) for journal states, units, render
metadata and recovery rules. These private artifacts are independent of Recorded
Sessions and Analysis Runs. A crash can leave output outcome unknown; that
uncertainty is not promoted to `played` or retried. Queue delay is not audible
latency. Expiry applies throughout the utterance: a long clip may be interrupted
before completion even if it started on time. A short template and fresh source
evidence are both necessary for useful operational communication.

## Remaining acceptance

Synthetic tests cover scheduling, freshness, duplicates, interrupted restart,
single-journal ownership, failure handling, DSP response and native API call
ordering through a fake device. Local file generation proves the installed
engine and DSP can produce WAV artifacts. These checks do not establish live
delivery or a useful reaction. Next checks are one coordinated headset/mute
check, device interruption/recovery, actual latency measurement, and integration
with fresh fuel evidence during ordinary driving. Preserve all unsuccessful
outputs. Do not ask the driver to induce a slide for this prototype.
