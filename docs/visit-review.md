# Explicit output reviews

The private review ledger records an assessment of exact output bytes, including insufficient, misleading and failed output. It does not generate a Coaching Instruction, change an Analysis Run, or infer what the driver thought or heard. A correction is another review; previous assessments remain intact.

`record_review(run, output=path, verdict=..., note=..., reviewer="chief", driver_feedback_confirmed=False)` accepts `useful`, `insufficient`, `misleading` or `failed`. Notes must contain 1–4000 characters. `chief` is the default role. A `driver` review requires explicit `driver_feedback_confirmed=True`: this records a declaration, not authenticated identity or evidence of audio delivery. Setting confirmation on a chief review does not turn it into driver feedback.

The chief can record an assessment through the visit CLI:

```powershell
uv run python -m simcoach.cli.visit review "<visit>" --output "<exact-output-path>" `
  --verdict insufficient --note "What this output establishes and what remains unknown"
```

Only after the driver actually supplies the feedback, add `--reviewer driver`
and `--driver-feedback-confirmed`. The driver can speak normally; the chief
operates these commands. Preserve the original assessment and add another review
when correcting it. `visit outcome "<visit>"` then writes a new snapshot including
all reviews and failures without replacing the host's original outcome receipt.

The selected output must resolve inside the visit's `outcomes`, `coverage`, `worker`, `clock`, legacy `output`, or an owned sibling `worker-[0-9]{4}` directory. This permits separate assessments of failed and replacement workers. Worker roots must resolve directly under the visit; external aliases are excluded. Root status/control files, capture-attempt launch receipts and review files are excluded. The recorder reads a regular file, checks path and open-handle identity before/after reading, and records its exact SHA-256 hash. The exact output is checked again before publication. A later changed output does not inherit an old review's assessment.

A live checkpoint review can publish while the host continues refreshing its
status. The review binds the same visit clock ID, PID and host process record
across two status observations. Both exact status bindings are retained. Older
visits without that identity and replay visits require the same status binding
before publication. A moved output or changed visit identity refuses the review.

Each invocation creates a new `reviews/<review-id>/` directory with `attempt.json`, followed by either `review.json` or `failure.json`. Rejected attempts and failed `.partial` files remain private. After flushing a new temporary file, publication uses [Windows `os.rename`](https://docs.python.org/3/library/os.html#os.rename), which refuses an existing destination. POSIX uses an exclusive hard-link operation because its rename can overwrite. There is no overwriting fallback. Windows needs no hard-link privilege. Invalid or inaccessible visit roots cannot retain an attempt; the call fails without selecting another destination.

`reviewed_at` is sampled from the local UTC clock during the explicit action; there is no supplied historical timestamp parameter. `review_latency_utc_s` is populated only for a live visit with matching, successfully published clock status/summary declaring absence, ordered UTC timestamps, and a matching processed departure request. Incomplete or changing departure evidence leaves latency null and can still yield a valid output review. This measures time from declared departure to this explicit review. It is not useful-debrief delivery time, response time to an event, or time until the driver heard anything. Local wall-clock accuracy remains a limitation.

No real driver feedback is created by the synthetic tests. They cover role confirmation, unfavorable outcomes, additive corrections, missing and failed clock publication, private path boundaries, input changes and publication failures. See the [versioned artifact contract](../schema/visit_review.md).

For a matched consistency follow-up, the Chief fixes one lap window and spread
target before the next Recorded Session begins. Choose comparable phases in both
sessions using their lap numbers, leaving warmup or the standing start outside
the planned window when appropriate. Record comparable conditions explicitly.

```powershell
uv run python -m simcoach.cli.dashboard_feedback plan-consistency `
  --visit "<baseline-visit>" --session "<baseline-session>" `
  --baseline-first 2 --followup-first 2 --count 3 --target-spread .5 `
  --conditions "<reviewed conditions and lap phase>"
```

Retain the printed plan path and SHA-256. After the follow-up session finalizes,
use `compare-consistency` with its `--visit` and `--session`, the
`--baseline-visit`, `--plan` and `--plan-sha256`, and the reviewed
`--conditions-match` (`matched`, `different` or `unknown`) and `--evidence`.
Run it while still seated if a checkpoint has finalized the intended block.
The report shows pace and spread separately when both windows qualify. Invalid
or missing attempts remain in the planned window and withhold the score. A
driver correction can be appended as a review of the exact report. See the
[comparison artifact contract](../schema/dashboard_feedback.md#private-consistency-plan-and-follow-up).
