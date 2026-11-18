# Chief readiness assessment

Status: private read-only pilot. A Windows process rehearsal joined a selected
collector and visit release using generated source data. The capture, recording
and fuel gates passed. With headset audio disabled and physical evidence absent,
the combined result held at NOT READY. The pilot currently targets the separate
operations-service workflow. Ordinary live arrival and automatic handoff remain
unverified.

A private [SimHub observation adapter](rig-observation-interfaces.md) now feeds
the game-data gate directly when explicitly selected. It requires exact reviewed
context labels, matching source scope and fresh native-bound observations. Other
rig gates remain independent; no new live readiness claim follows.

The assessment joins the current reviewed intention and private rig requirements
with explicitly selected visit, archive and release artifacts. It never selects
the newest directory, starts a process, declares presence, arms fuel, unmutes radio
or sends input. A report-generation exit code of zero means the report was written,
not that the rig is ready.

| Evidence | Required distinction |
| --- | --- |
| Visit and recording owners | Native PID plus creation identity, fresh status, coherent artifact identities and advancing recorder ticks; process presence alone is insufficient. |
| Independent capture | Advancing source samples, coherent clocks, supported layout and the selected collector's identity. |
| Storage | Fresh free-space observations at the selected archive and current recorder destinations, compared with the reviewed remaining write budget and reserve. |
| Session | Fresh car, track/layout and mode from telemetry; explicit observation of opponents, session length and conditions against the intended setup. Participant count cannot substitute for opponent count. |
| Fuel | Exact reviewed plan including stint length and reserve, current plan ID, loaded-fuel approval, healthy armed supervision, matching source/context/viewed slot and service progress. |
| Radio | Available, unmuted service state plus the loaded selection hash and separately scoped hearing evidence. Configuration and playback do not establish hearing. |
| Rig and handoff | Current device connections, approved profiles, game connection, distinct haptic/headphone routes, maintenance exclusion and driver control. |
| Selected source | Verified release contents and matching role launch receipts bound to native process lifetimes. A release ID in status alone is insufficient. |

The fuel gate binds the operations service's observed slot to the collector's
validated `latest.participant_observation`. Missing slot evidence stays NOT RUN.
An inactive, inconsistent or mismatched slot fails the gate. A verified slot
is scoped to the current source and still requires cockpit fuel and player
confirmation through the existing approval workflow. The archive and operations
reading must agree on the continuity generation. This prevents a fresh-looking
old service status from establishing fuel readiness after capture has recovered.
The assessment requires new service progress under the matched generation.
Legacy sources and service readings may both lack generation evidence.

The private pilot also binds `collector_release_id` from the reviewed intention
to a separately verified collector snapshot selected with `--collector-release`.
The exact archive's `run.json` and its launch attempt's `launch.json` must name
the same release, archive parent and native lifetime. Both files are rechecked
across the status read, with native owner probes before and afterward. A legacy
archive or absent binding leaves this gate unresolved. The process gate and
advancing capture gate remain separate.

For initial switch readback, the private observer distinguishes `BOUND_IDLE`
from `BOUND_SOURCE_ADVANCING`. Both require a matched native owner and two
fresh, coherent advancing heartbeats. Idle additionally requires the mapping
unavailable state and unchanged sample count. Advancing source requires two
supported advancing source observations. This reads one explicitly selected
archive and never chooses the newest directory. The result has its original
source deadline. It gives no Task Scheduler, physical mapping provenance or
whole-rig handoff verdict.

External observations remain Chief-reviewed evidence, not automatically inferred
facts. Their claims bind the intention, rig requirements, source, host lifetime,
service lifetime, plan and audio selection. The pilot checks artifact hashes,
method and expiry. These checks establish reference integrity, not semantic truth
or independent authentication of the reviewer. Missing or unsupported evidence is
NOT RUN; explicit contradictions are FAIL. Preserve both with the next needed check.

The Chief supplies `storage_budget` in the intended stint with estimated
`archive_write_bytes`, `recording_write_bytes`, `reserve_bytes` and a short
reviewed `basis`. Include expected recording and closeout writes. The reserve
must be at least the collector's current 1 GiB floor. The pilot requires the sum
on each destination so two paths sharing one volume cannot independently spend
the same free bytes. This test is conservative when they use separate volumes.
Space is observed before handoff with a three-second expiry. Low space is FAIL.
Missing budgets, inaccessible destinations and unavailable readings are NOT RUN.
The snapshot provides no filesystem reservation, quota guarantee or forecast of
unrelated writes. The collector continues to enforce its own reserve during capture.

Read expiry after all proof-file I/O using the owner's UTC and monotonic clocks.
Recorder intake expires three seconds after the last tick, taking its already
reported age into account. Reading a worker status again does not renew it.
The preparation report samples long enough to span an ordinary collector
publication interval and may end early on passing evidence. It includes the
final expiry and withholds any assessment already expired at publication.
An expired component or clock discontinuity prevents a complete result. The
pilot's short validity windows are provisional engineering policy, not measured
user-experience targets. Reuse hearing/calibration evidence only within its verified
scope; a stale report does not by itself justify another driving exercise.

Even an entirely passing synthetic assessment reports only readiness for Chief
review and leaves `ready_to_drive=false`. Actual off-track evidence, supported
observation/input integration and the Chief's handoff remain necessary. A closed
historical visit reported NOT READY using a synthetic negative-control intention;
that exercise established neither a current fuel plan nor live rig readiness.

See [arrival procedure](returning-to-the-rig.md), [operations service](operations-service.md),
[radio configuration contract](../schema/operations_service.md) and
[input orchestration](input-orchestration.md).
