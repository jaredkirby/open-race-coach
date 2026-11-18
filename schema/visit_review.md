# Explicit visit output review

Schema version: `1`.
Owner: `simcoach.live.visit_review`, invoked by an explicit review action.
Write timing: one exclusive new private `reviews/<review_id>/` directory per attempt, then `attempt.json`, then `review.json` on acceptance or `failure.json` on error. Flush and fsync precede exclusive atomic publication: Windows uses non-overwriting `os.rename`; POSIX uses an exclusive hard link. There is no overwriting fallback. Failed `.partial` files remain; previous files are never overwritten.
Compatibility: additive diagnostic contract. Consumers must reject unsupported versions and preserve old reviews as historical assessments of exact output bytes. Corrections create additional reviews; there is no automatic replacement, identity authentication or verdict aggregation.

## Accepted review

Every field below is required; nullable fields and units are explicit.

| Field | Contract |
| --- | --- |
| `schema_version` | integer `1` |
| `review_id` | 32 lowercase hexadecimal characters; directory identity |
| `reviewed_at` | finite nonnegative UTC Unix seconds sampled during this action; no backdating input |
| `visit_mode` | enum `live`, `replay`, from bound parent status |
| `output` | source binding for one regular file inside visit `outcomes`, `coverage`, `worker`, owned sibling `worker-[0-9]{4}`, `clock` or legacy `output`; worker roots resolve directly under the visit |
| `visit_source` | source binding for first supported parent `status.json` observation |
| `verdict` | enum `useful`, `insufficient`, `misleading`, `failed`; independent of reviewer role |
| `note` | trimmed nonempty string, original input length at most 4000 characters; private |
| `reviewer` | enum `chief`, `driver` |
| `driver_feedback_confirmed` | boolean; true only for an explicitly confirmed driver review; chief reviews always false |
| `reviewer_identity_authenticated` | always false; role is a declaration |
| `delivery_verified` | always false; review is not delivery |
| `audio_heard` | nullable, always null in this contract |
| `declared_departure_at` | nullable UTC Unix seconds; accepted clock departure only |
| `departure_sources` | array of source bindings read during departure verification; may be empty/partial even when latency is null |
| `review_latency_utc_s` | nullable nonnegative seconds, `reviewed_at - declared_departure_at`; not a useful-debrief/heard metric |
| `latency_kind` | literal `explicit_review_after_declared_departure` |

Latency requires live mode, supported matching terminal clock summary/status, `status=complete`, `terminal_intent=complete`, `publication_complete=true`, null publication error, `presence=declared_absent`, nonempty visit ID, finite elapsed duration, ordered UTC start/departure/review timestamps and a `stop` declaration of `driver_left` whose finite timestamp matches the clock departure within one microsecond. Replay, interrupted/unpublished/mismatched clock state, invalid time or missing departure gives null latency. A stop request by itself is not processed departure evidence.

Every source binding requires `path` (absolute resolved private locator), `sha256` (64 lowercase hexadecimal characters), `bytes` (nonnegative integer count), `device` (integer filesystem device value), `file_id` (decimal text of native inode/file identity), and `modified_ns` (decimal text of filesystem modification time in nanoseconds). Path and open-handle identity are checked around reads. The selected output and any departure evidence used for timing must still match their exact bindings at recheck. Changing departure evidence withholds latency while retaining the observed source bindings.

Current live reviews additionally record `visit_identity` ({`visit_id`,
`pid`, `host_source`}) and `visit_source_before_publication` when the clock
ID and the PID match the same exact `host-process.json` record across both
observations. In this case the parent status file may publish new bytes
between snapshots. Each snapshot's binding describes the exact bytes read,
irrespective of later updates. A known parent clock ID must match terminal
clock evidence before crediting departure. Legacy and replay reviews
without a matched live identity require exact status recheck. The added
fields are optional for readers of version 1.

These are local filesystem observations, not authenticated provenance. Review output and control paths cannot be selected as review targets; resolved paths outside the chosen visit are never read.

## Attempt and failure evidence

`attempt.json` requires `schema_version=1`, `output` (requested locator string), `verdict`, `note`, `reviewer`, and `driver_feedback_confirmed`. Invalid input types for the latter four fields are retained as nullable values; supplied strings are preserved even when rejected. Attempts do not imply acceptance, review completion or delivery. Invalid/inaccessible roots cannot store attempts.

`failure.json` requires `schema_version=1`, `status=failed`, `error_type` and `reason` strings, and `review_accepted=false`. Filesystem failure may leave only the attempt or partial evidence. A failed publication is never accepted from a partial file. Successful API return requires `path` (accepted review locator) and `review` (the exact published object). No canonical capture, Analysis Run, clock, output or prior review is rewritten.
