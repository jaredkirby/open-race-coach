# Radio prototype artifacts

Schema version: 1.
Owner: `simcoach.live.radio`.
Write timing: generated WAVs and render metadata are published from
same-directory partial files; journal events are appended, flushed and synced
before external output. A dispatcher holds an OS lock on its journal until close.
Compatibility: unknown journal versions, corrupt lines and incomplete histories
are refused, preserving the original file. No automatic repair or audio replay.
These artifacts do not change Recorded Session or Analysis Run contracts.

## Journal

Required fields on each JSONL event:

| Field | Meaning and units |
| --- | --- |
| `schema_version` | Integer 1 |
| `at` | Finite UTC epoch seconds; observed event time |
| `transmission_id` | Stable occurrence identity, separate from the WAV locator |
| `state` | Delivery state enum below |
| `reason` | Machine-readable reason for this transition |

`generated` additionally requires `spec`: `transmission_id`, `dedup_key`,
`evidence_id`, `wav_path`, `priority`, `created_at`, `expires_at`, `optional` and
`wav_sha256`. IDs are nonempty strings of at most 256 characters. Priority is an
integer 0-100, higher first. Times are finite UTC epoch seconds with
`expires_at > created_at >= 0`; expiry applies throughout playback, not just
to starting it. `optional` is boolean. WAV paths are private locators and
SHA-256 binds the queued content. Deduplication keys identify an event occurrence,
not a reusable phrase. A repeated ID with a changed specification is refused.

State enum: `generated`, `queued`, `playing`, `played`, `expired`, `suppressed`,
`interrupted`, `failed`, `acknowledged`. On restart, unfinished `generated`,
`queued` and `playing` states become `interrupted` with `restart_no_replay`.
No unfinished message is automatically replayed. Known IDs/keys remain consumed
even if expired, suppressed, interrupted or failed.

`playing` records durable output intent. A following failed or expired event can
mean no audio was emitted. `played` means the transport reported completion,
not that a headset produced sound. Optional measurement fields:
`queued_delay_s` (seconds from message creation to output intent), nullable
`audible_onset_at` and nullable `driver_heard`. They remain null without evidence.
`acknowledged` requires explicit driver confirmation after completed playback.

New journal rows include `timing_epoch` (one opaque ID per dispatcher instance)
and nullable `monotonic_s` from its local performance counter. Fuel
return `generated` rows also carry `source_at` when the notice preserves the
original source observation in UTC epoch seconds. This is the archived
observation time, without an inferred physical threshold-crossing time.
The first terminal transition after a transport start attempt carries optional
`transport_start` with `requested` and nullable `confirmed` timing points.
Each point contains `at`, `timing_epoch` and `monotonic_s`. Confirmation means
the transport returned true. The dispatcher adds no separate write to the
playback path. Death before terminal publication can leave confirmation unknown.
Older rows may omit these fields. Timing fields are diagnostic and do not change
expiry, transition/restart policy, or output authority.

The fuel adapter can end a playing intent as `expired` with reason
`transport_start_guard` when evidence ages during the final permission read.
The transport confirms no start in this case. A clock regression can fault
the owner before it can publish an ordered terminal event. Treat an unfinished
intent as unresolved and apply the existing restart rule.

A partial line or missing original generated specification makes reopening
fail. Preserve that journal; do not discard the tail and replay messages.
The lock is per journal, not machine-wide arbitration across different files.

## Native output ownership diagnostics

The Windows transport additionally holds a session-local named-object claim
across journals. It is acquired before dispatcher initialization completes and
released only after confirmed native stop/device release on explicit close.
This adds no output permission to journal replay; interrupted messages remain
consumed. Claim creation/access/name conflicts refuse output. Failed closure
keeps the native handle and journal owner available for closure retry. A process
exit releases its native handles; a thread exit alone does not release this
process-lifetime claim. No timed lease, forced takeover or competing speaker is
created. This is cooperative exclusion for current native ORC transports within
one Windows session, not machine-wide endpoint ownership.

Fuel-radio snapshots add nullable `output_ownership`. Null means the injected
transport provides no such diagnostics. A native snapshot requires
`schema_version=1`, `scope=windows_session`, `state`, nullable `reason`, and
`audio_health_verified=false`. State enum: `unclaimed`, `held`, `unavailable`,
`closed`. Held is output exclusion, not headset delivery or a live health probe;
read it only as part of a fresh owner's status. Close failure may retain held
with a reason. A later confirmed close may retain that historical reason.
No timestamps are added to this nested object; its enclosing observation owns
time units and freshness. Existing fake snapshots may omit this additive field.

The [operations service](operations_service.md) adds a fixed chief-connection
warning through this same dispatcher. Its phrase, occurrence evidence, priority,
expiry and additional snapshot fields are defined there. It is not an arbitrary
remote speech endpoint or permission for the observer to acquire audio.

## Render package

`dry.wav` and `radio.wav` are mono PCM16, 8-48 kHz, positive duration at most
30 seconds. The installed voice renderer currently requests 22.05 kHz. DSP
preserves rate and frame count. A new output directory is required; failed
partials remain and retries use a fresh directory.

Required `render.json` fields: `schema_version`, `status` (`generated`), `text`,
`engine`, `voice`, `powershell_executable`, `dry_sha256`, `radio_sha256`,
`dsp_version` (1), `intensity` (0-1), `sample_rate_hz`, `frames`, `duration_s`,
`synthesis_s`, `render_total_s`, `audio_output_attempted` (false), and nullable
`audible_onset_s` (null). Duration measurements are seconds from a performance
counter; generation time is not event-to-audible latency.

Failed rendering writes `failed.json`: required `schema_version`, `status`
(`failed`), `error_type`, `error` and `audio_output_attempted` (false).
Synthesis stdout/stderr and partial WAVs may also exist. Text, voice information,
paths and error details can be private. Do not publish unreviewed packages.

## Evidence limits

The journal records the dispatcher's decisions and transport results. It does
not prove correct telemetry, driver attention, hardware routing, audible onset,
intelligibility or coaching effectiveness. See the [radio prototype](../docs/radio-prototype.md)
for the operating boundary and remaining hardware checks.

## Private output inventory and selection

The read-only inventory CLI returns `schema_version: 1`, `devices`,
`inventory_health` (`available` or `unknown`), nullable `reason`,
`audio_output_attempted: false`, and `route_verified: false`. A host saving this
snapshot must record its observation time separately; inventory alone has no
freshness guarantee. Empty devices means no outputs or an inventory error,
which the native count API cannot distinguish.

Each device record requires `index`, `name`, `manufacturer_id`, `product_id`,
`driver_version`, `formats`, `channels`, `support`, `fingerprint` and `selection`.
Numeric fields are native integer capability values; `formats` and `support`
are bit masks, and index is the current zero-based inventory position.

Selection schema version: 1. Required fields are exactly `schema_version`,
`name` and `fingerprint`. The name must be nonempty and fingerprint a lowercase
64-character SHA-256. The fingerprint is computed from UTF-8 encoding of Python
`json.dumps(fields, sort_keys=True)` with default separators/ASCII escaping,
where fields are the device name/capability fields above excluding index,
plus `schema_version: 1` and `backend: "winmm_caps"`. The saved selection
does not use a numeric device index as durable identity.

The transport resolves a unique expected name/fingerprint and revalidates
capabilities before open and before buffer submission. It refuses ambiguous or
missing matches instead of falling back to a default. The legacy no-selection
mode remains explicitly opt-in at transport construction and is separate from
selected-output behavior. These name/capability fields are not guaranteed
physical endpoint IDs; truncated names and indistinguishable hardware limit
identity checking. Product/device names may be private. Unknown selection
versions, missing/extra fields or malformed fingerprints are rejected.
