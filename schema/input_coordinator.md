# Synthetic Input Coordinator journal

Schema version: 1. Owner: `simcoach.live.input_coordinator`.
Write timing: admission and dispatch commit before a simulated effect; terminal
observations, revocation and explicit reconciliation append events afterward.
Compatibility: new private SQLite artifact. No existing recording, agent runtime,
desktop controller or installed entry point is migrated. No live input is supported.

## Storage and ownership

Required file: input.sqlite, with SQLite application_id `0x4F524349` and
user_version1. WAL/SHM sidecars are SQLite-managed durable working files; preserve
them after a crash. WAL and synchronous=FULL are configured, not claims of
power-loss durability. Existing databases with another identifier/version are
refused; quick_check must pass. Root/database path redirection is refused. This
is trusted local storage, not protection from hostile file replacement or a
security sandbox. Database changes and event insertion occur in one transaction.

A fixed Windows-session native object excludes cooperating synthetic coordinators
across journal directories. Tests inject private names. It does not own the actual
desktop, exclude unrelated controllers, authenticate the Chief or observe hardware
takeover. No radio/collector claim is acquired. An in-flight action or unconfirmed
simulated release prevents normal owner close; confirmed cleanup may close an owner
whose storage is already broken, while retaining its error. Native process death
releases its handles but never proves a prior effect did not occur.
New preparation grants are also refused while any prior release operation remains
in progress; a late cleanup cannot overlap a newly granted generation.

commands required columns: id (primary key), fingerprint (SHA256 of canonical JSON),
payload (immutable request JSON), state (enum), reason (nullable string), ordinal
(unique integer admission order). Maximum10000 retained commands per journal.
Repeated exact ID/content returns its existing receipt; conflicting content is
refused. A duplicate terminal request never dispatches again.

events required columns: seq (integer primary key), at (UTC Unix seconds), kind
(enum), value (JSON). Kinds: owner_opened, preparation_granted, command_accepted,
command_state, late_result, control_revoked, synthetic_inputs_released,
chief_reconciliation. Events are append-only in normal operation. Command state
is a current projection; events preserve transitions and late results. Failed
storage revokes in-memory authority and stops further effects. The failed write
itself may not be durably logged; preserve database and external error evidence.

The optional synthetic gateway adds gateway_opened, gateway_bound and gateway_intake
and gateway_extended events under the [gateway contract](input_gateway.md).
Reviewed recipe compilation adds recipe_step_compiled under
[session_recipe.md](session_recipe.md). The gateway never persists credentials
or grants input authority through its transport.

## Command and observation

Required command fields: id and generation (32 lowercase hex characters),
recipe_sha256 (64 lowercase hex), before (observation), action (enum: select),
target, expected_state and deadline. No unknown command fields are accepted.
The recipe hash is an asserted reference from the trusted caller, not proof of a
loaded or reviewed recipe. The separate synthetic recipe compiler verifies its
review and observation bindings before compilation; a complete SAS adapter is not
implemented. The optional gateway binds a host-asserted principal with an ephemeral
bearer; this is not OS/account authentication. Text labels are1–128 characters
without control characters.

Observation required fields: at, revision, app, pid, created, window, state,
focused, modal, driving, targets. PID/window are positive integers; revision is a
nonnegative integer. App/creation/state are bounded labels. Conditions require
explicit booleans; unknown driving is refused. Targets are at most64 unique labels.
These are generated test observations, not native process or UI verification.
Admission can accept an absent old target when the expected destination is already
observed. Dispatch rechecks the destination and records skipped without input;
changed state still requires the target and original basis to match.

Time unit for at/deadline is performance-counter seconds within one owner lifetime.
Admission requires observation_at <= now < deadline <= observation_at+2s. Before
dispatch, recheck deadline, fresh observation age<2s, unchanged generation,
no unknown prior effect, focused target, no modal and not driving. Native target
identity fields, revision, source state and targets must match the basis. A fresh
already-observed destination may skip the action without claiming it caused that
destination. Postcondition must be fresh, before deadline and match destination
and target identity. Clock regression revokes authority.

The fake executor independently rechecks cancellation, the original observation's
time bound, command deadline, conditions and complete basis immediately before
applying its effect, under its state lock. This occurs after the dispatch journal
commit and any injected executor delay. Expiry, clock regression/nonfinite time,
focus/modal/driving changes, target identity, revision, state or selector changes
prevent the simulated effect. Even a destination reached independently after the
commit is not credited to that command. The committed dispatch remains unknown
and requires reconciliation; backend refusal does not authorize automatic retry.
This scoped fake-state lock is not proof of atomic Windows observation and input,
nor an actual OS sleep/resume check. Future backends must enforce equivalent final
checks through their supported observation/input interface.

## States and recovery

State enum: queued, dispatched, verified, skipped, refused, cancelled, unknown,
reconciled. Dispatched commits before calling the fake backend. Only a supported
fresh postcondition in the same generation yields verified; this is a simulated
destination observation, not a claim of physical exactly-once execution.

Takeover signals cancellation, clears generation, cancels queued commands and
makes dispatched commands unknown. It attempts simulated held-input release even
when journal writing fails. Late results cannot overwrite a prior terminal
revocation result. No real hardware takeover listener or latency guarantee exists.

Startup clears permission, marks old queued commands cancelled and old dispatched
commands unknown. It never resumes old actions or deadlines. Unknown outcomes block
fresh grants until a trusted Chief explicitly reconciles each command with a note.
Reconciliation marks it reconciled with no replay; the physical outcome remains
unknown. It is not a conclusion that the input was absent or safe to repeat.
Fresh preparation requires a new generation; reason/note are1–512 characters.

The only backend is SimulatedInput. Dispatch rejects other types. Trusted test
callbacks can inject failures; this is a testing seam, not a code sandbox. There
is no OS input backend, persistent agent deployment, full SAS implementation or
live simulator acceptance in this artifact. The optional gateway exposes bounded
loopback intake, inspection and cancellation only; grant and dispatch stay local.
