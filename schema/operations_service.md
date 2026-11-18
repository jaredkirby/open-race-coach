# Operations service artifacts

Schema version: 1. Owner: `simcoach.live.operations_service`.
Write timing: new `run.json` at creation; atomic heartbeat/status on each poll;
request journal and receipt before considering that request processed; terminal
journal before complete status. Compatibility: additive version-1 operational
artifacts; existing visits and Recorded Sessions are unchanged. Unsupported
request versions are refused. Fresh output directories never resume old service
authorization, pending output or fuel approval.

Integrated visits may use the fixed child directory `visit/operations`;
otherwise output must be separate from the visit tree. Root/ancestors cannot be
output. `run.json` also records `visit_controls` boolean. When true, the mute
file and its receipts use the visit path; fuel requests/journals remain in the
service directory. Both stop locations are checked. No capture/clock authority
is transferred.

Parent launch intent precedes its single spawn and hashes the serialized plan.
The client pins service ID and actual PID/creation identity; replacement latches
failure. Fuel forwarding intent precedes request publication and hashes exact
platform text bytes. Ambiguous publication stays pending and is never retried.
Only matching service-ID/request-hash receipts are copied to the parent with
`authority=operations_service`. Copy failures retain pending state and errors;
copy retry cannot repeat approval. Conflicting receipts and failed unique
temporaries are preserved. Intent alone proves no external effect.

## Binding and state

`run.json` required fields: `schema_version`, random `service_id`, absolute
`visit` and `archive` locators, `process` identity, `pinned_host` identity, `at`,
`duration_s` (0.1–7200), `interval_s` (0.05–0.25), `plan`, `audio_enabled`,
`injected_dependencies` (source/inspector/transport booleans) and `mode` enum
(`injected`, `local_operations`). Unix timestamp and duration units are seconds;
identity creation times retain decimal-text FILETIME units. A process receipt
does not prove current liveness. `operations-service.lock` in the visit is an
OS-held lock, not a timed lease; a file remaining on disk does not prove an owner.

Running `status.json` requires `schema_version`, `service_id`, `status=running`,
`updated_at`, `declared_presence=true`, `ready_to_drive=false`, `chief_link`,
`supervision`, `radio`, `driver_departure_inferred=false` and
`capture_health_verified=false`. `heartbeat.json` requires version and `at`.
New status also has nullable `observed_source`: source_id (archive hash), at
(source UTC Unix seconds), sequence, nullable participant_index and Context,
all from the current Supervisor Reading. This includes the optional
`continuity_generation` from the selected archive when the reading has it.
`observed_source` is null without a current reading. The visit host and dashboard
withhold active fuel claims when the current archive generation and service
reading disagree or only one supplies a generation. The host retains the
reported plan and the unmodified service status as diagnostic evidence.
Valid legacy snapshots can omit the generation on both sides.
Terminal status enum: `complete`, `incomplete`; requires `stop_reason` and final
radio state. Complete describes service closure only. An immediate stop may
have no observation fields. A confirmed driver-left declaration clears declared
presence; timeout/closure/failure never invents departure. Missing/stale status
after failure is not an operational health claim.

`chief_link` requires `state` (`awaiting_connection`, `connected`, `lost`), `at`,
nullable `pinned_host` and `liveness` assessment. A lost episode also requires
`loss_id` and fixed `created_at`. Repeated reads update observation time without
renewing that episode's deadline. Reconnection clears the episode; future loss
has a new occurrence. Changed host identity remains latched by VisitLiveness.

## Fuel requests and controls

Schema-version-1 `action=plan` additionally accepts `plan` (versioned FuelPlan
object), `expected_plan_id` and `note`, with the same required fresh `at` and
service instance binding. The chief connection must be verified at consumption.
It follows [explicit replacement](race_watch.md#explicit-plan-replacement), never
fuel approval. Receipts now also include nullable `action` and current `plan_id`.
The parent hashes exact forwarded bytes and copies only a matching receipt, using
the same failure/ambiguity handling as fuel forwarding. The original startup
plan remains in run.json; current plan and revision are in supervision status.

The new service's `requests/*.json` accepts schema-version-1 fuel commands with
required `service_id`, `action=fuel`, fresh `at` (age 0–less than 3 seconds), and
`approval` containing the existing Supervisor.arm fields: cockpit litres,
verification timestamp, source identity, solo-player confirmation and note.
Optional `player_view_confirmed` and `participant_index` are forwarded unchanged
and included in the exact request hash. The Supervisor enforces mutually
exclusive legacy-solo versus explicit viewed-slot approval; the service does not
infer identity from a slot or turn a forwarded intent into approval.
The service instance and a currently connected chief must match. Filename is
the local request identity; it is consumed once per fresh service. Receipts
bind `request_sha256` (nullable for unreadable artifacts), `accepted`, `applied`,
nullable `reason`, timestamp and service identity. Rewriting a consumed filename
does not apply new intent; clients must compare receipt hash to the request.

Fuel admission rereads the selected archive after request and connection reads.
The existing Supervisor validates that observation with its original source
timestamps and continuity rules. Before a fuel or plan decision, the service
holds both owners' request locks and checks published departure, elapsed service
duration, clock coherence, request age and Chief heartbeat age. Contention
leaves the exact request unconsumed. An observed stop or expired boundary
refuses the decision. New receipts and `service_request` journal events add
nullable UTC Unix `decision_at` for the time passed to the Supervisor. A null
value means the command was refused before reaching the Supervisor. Receipt
`at` remains publication time. Older results may omit `decision_at`.

`mute.json`, its immutable receipts and priority watermark retain VisitAudio's
existing contract. The service allows fresh unmute only while chief-connected;
existing unmuted output can continue after disconnect. Mute remains valid.
VisitAudio binds permission to the bytes of the accepted Unmute. The final
transport guard rereads the bounded priority file and denies output when the
bytes no longer match or cannot be read. A semantically identical rewrite can
be recognized by the owner loop. Missing previously accepted control, an I/O
failure or a control larger than 65536 bytes revokes the grant and reports
degraded audio. This adds no receipt fields and does not change fuel approval,
capture, or the meaning of a prior control acknowledgment.
The `stop` file in the visit or service uses existing `at` and
`declaration=driver_left`; unreadable/invalid stop evidence closes operational
output without claiming departure. No automatic service restart is supported.

## Journals and radio

VisitAudio's `radio.configuration` is an additive schema-version-1 object with
`transport` (`selected_windows_wave`, `injected_factory` or `unconfigured`) and
nullable `selection_sha256`. The selected transport records SHA-256 of the exact
validated selection bytes loaded at construction. Later file edits do not change
that receipt. Injected factories do not assert native output selection. Missing
configuration in older statuses is unknown. This binds loaded configuration only:
it proves neither current device availability, successful output nor hearing.
Use it with fresh delivery state and scoped human hearing evidence, never alone.

`supervision.sqlite3` uses the Supervisor Journal metadata/event contract and
existing observation, fuel-arm, loss and notice events. Added event enum:
`chief_link` (assessment and nullable full inspection), `service_request`
(path, nullable hash, accepted, reason), `service_stopped` (reason, audio_closed),
`service_failed` (error_type). Every event has schema version and Unix `at`.
Failure publication is best effort and never overwrites prior evidence.

The existing radio journal retains delivery/no-replay rules. Chief-loss
transmissions use identity/dedup `chief_loss:<loss_id>`, a hash of the loss
identity/creation time/pinned host, priority 90, and fixed five-second expiry.
Snapshots add `chief_loss_warning_configured`, nullable
`chief_loss_transmission_id`, and `chief_loss_delivery_state` (existing delivery
enum plus `none`). An unsuccessful current warning degrades delivery health
unless muted. Played is transport completion only, never hearing.
