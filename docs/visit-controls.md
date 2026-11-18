# Local visit controls

Open this small local window explicitly before driving, for an existing Driver
Visit. It does not start a visit, record telemetry or select an audio device.

```powershell
uv run python -m simcoach.live.visit_controls --visit "<existing visit directory>"
```

The window uses Python's standard Tkinter module. If Tkinter or a display is
unavailable, the command reports an error. It provides Mute, Unmute, End visit,
and a reason selector for non-driving time. Normal keyboard Tab navigation and
button activation are available. There are no global hotkeys, forced focus,
always-on-top behavior or later automatic window raising. Arrange it before
driving; opening it explicitly may activate the new window. Closing the window
does not end the visit or change mute state.

Mute is a safe inhibition request and remains available for a known visit even
when its heartbeat is stale or it is closing. Unmute, departure and reason changes
require a fresh running coordinator, explicit declared presence and no previous
departure request. The client checks both the visit status and heartbeat; each
must be less than three seconds old and not in the future. A stale coordinator
may not handle even a successfully written Mute request: treat it as requested,
never confirmed silence. Use the rig's existing hardware mute if immediate silence
is needed while the coordinator is unavailable.

The capture indicator expires at the deadline in the host status, even while the
controls remain available. A status from an older host without a capture deadline
provides no current capture indication.

End visit records the explicit departure's UTC wall timestamp and matching
`perf_counter()` timestamp. The first boundary is preserved. Reasons apply through
the coordinator prospectively; they do not rewrite prior clock totals. Rest is
not labeled wasted time. The UI distinguishes request submission from the
coordinator's acknowledgment; neither establishes what the driver heard.

The host serializes clock updates with departure publication. The controls
audit keeps the original click/intent time and the accepted boundary time
separately; the latter is sampled under the shared lock. An observation cannot
advance the clock past a departure while its stop file is still being written.
This prevents an ordinary source-read/publication race from turning a valid
departure into an interrupted clock. The boundary is request acceptance, not
an independently measured physical departure.

New visit clocks clear a reason after a supported driving interval, so a later
stop begins with `unspecified` unless a new reason is declared. Pit movement and
unknown observations do not clear it. The chief handles these declarations;
the driver need not operate the selector during driving.

All clients cooperate through the visit's `request.lock`. Mute writes only the
priority `mute.json` state, with `schema_version`, `command_id`, `muted`, `at`
and an `intent_watermark` retaining the newest intent timestamp. Under the lock,
an older Unmute cannot replace a newer control. The host also rejects reordered
or conflicting commands; changing a payload under an already seen ID fails closed.
It is not queued a second time: replaying an old queued Unmute after a later Mute
would be unsafe. The coordinator polls this state and writes its acknowledgment
to `receipts/<command_id>.json`. Reasons use `requests/<command_id>.json`; departure
uses the existing `stop` boundary. The coordinator starts audio muted until a
fresh explicit Unmute is accepted; the UI itself never restores or plays audio.

Playback requires the current priority control to match the accepted Unmute,
including after the device has prepared a buffer. A Mute received during
preparation can block a pending phrase before the output call. If the control
file becomes unavailable or oversized, the owner inhibits output and requires a
fresh Unmute. Interrupted or expired phrases stay consumed. The file check and
device call are separate operations, and the owner still requires confirmed
transport stop for any phrase already playing.

Each control attempt preserves a private unique `controls/<command_id>.intent.json`
and `.result.json`, including refused or failed writes. These are client receipts,
separate from coordinator acknowledgments. A write followed by missing acknowledgment
is unresolved, not delivery success. Preserve logs and partial files when diagnosing
failures. The known visit directory and its `requests`/`receipts` directories must
already exist; this component never guesses the newest visit.

Controller protocol, separate-process locking and failure handling are tested
headlessly. Actual Tk window usability, simulator focus behavior and audible
mute/delivery are **NOT RUN** until explicitly exercised on the rig. No live
clock or audio starts by importing this module or running its tests.
