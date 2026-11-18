# Cooperative standalone recording control

The normal Driver Visit host remains the intended owner of capture, presence,
fuel supervision and debrief. If the chief needs standalone recording during
recovery, a controlled run provides a persistent stop request instead of depending
on a console interrupt reaching the correct child process. It does not run a
driver clock, infer presence, enable audio, or replace the visit coordinator.

Chief-operated commands, from the repository root:

```powershell
uv run record --sim ams2 --control-dir .scratch/v/recovery-example
uv run record --stop .scratch/v/recovery-example
```

The first command runs until explicitly stopped or a terminal capture failure.
Use a **new directory for every run**. It owns `sessions/`, `run.json`,
`status.json`, and closure/debrief outputs inside that directory. `--out` and
`--max-seconds` cannot be combined with `--control-dir`. The legacy command
without `--control-dir` retains its existing behavior.

The stop command is issued from another chief process. Exit zero means the
request was saved, already present, or the run was already terminal; its message
distinguishes these cases. It does **not** mean the recorder has finished.
Inspect the matching run identity in `status.json`, then `closure.json`:

- `complete`: recorder returned and all inventoried Recorded Sessions passed
  artifact validation and deterministic analysis. This does not establish a
  useful Coaching Instruction, live readiness, or a successful Driver Visit.
- `cancelled`: stopped before stable metadata; no Recorded Session exists.
- `failed`: capture, finalization, validation, analysis or control failed.
  Preserve partial outputs and diagnostics; do not retry into the same directory.
- `starting`, `recording`, `stopping`, `closing`: still nonterminal. A stale
  heartbeat is uncertainty, not evidence the owner exited.

The existing continuous source-gap policy seals a supported prefix and retains
ownership while waiting for usable observations. Stop works during metadata wait,
normal capture and source-gap wait. Once accepted, stop does not expire. Blocked
adapter calls or filesystem I/O can delay completion; no signal, force-kill or
automatic replacement is attempted. Inspect the real owner before recovery.

Runs sharing a parent use its `capture.lock`, compatible with Driver Visit workers
under that same parent. This is **not machine-wide exclusivity**: different
parents and legacy recorders do not share the lock. Check existing processes
before starting; a running legacy recorder cannot adopt these controls after launch.

Run tokens bind a request to one fresh directory; they are not authentication.
See the [control contract](../schema/record_control.md). Completed captures and
Analysis Runs retain their existing contracts. No Coach Refinement runs here.

This path has offline lifecycle tests. Actual Windows simulator closure using
these new controls, process-restart recovery, and the full ordinary-visit gate
remain unverified until observed. Keep the independent raw archive active under
the driver's capture preference throughout recovery.
