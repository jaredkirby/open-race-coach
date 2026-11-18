# Between-drive Rig Engineer

The Windows Rig Engineer runs a short preflight when the computer is
idle. It can flag an AMS2 update waiting in Steam, a Windows device problem, or
a changed controller profile before the driver sits down. Each observation has a
timestamp. The Race Chief still needs current simulator and physical evidence at
handoff.

| Check | Evidence and follow-up |
| --- | --- |
| Steam | Reads the selected AMS2 app manifest, including installation flags, build, download and staging counters. Unfinished work points the driver to Steam Downloads. This reflects Steam's last local knowledge. |
| MOZA USB | Checks exact configured Plug and Play devices, their current presence and Windows problem codes. A base normally powered off between visits is listed as waiting for connection. A Windows device fault creates an alert. Wheel and pedals behind the base still need Pit House or simulator evidence. |
| AMS2 controls | Hashes the configured saved controller profile. The first observation establishes a comparison point. A changed file stays flagged across restarts until it returns to the baseline or the driver verifies and accepts the new bindings. A missing file has its own result. |

## Running it

Use Windows, Node 22.19 or later and the project's Python 3.11+ virtual
environment for AMS2 input checks. From the repository root:

```powershell
cd agents/rig-preparation
npm ci --ignore-scripts
node preflight.mjs once
node preflight.mjs status
```

`watch` repeats every 15 minutes in the current process. For unattended use,
`register-preflight-task.ps1` creates the current-user **Open Race Coach Idle Rig
Preflight** scheduled task at logon and every 15 minutes. It leaves sleeping PCs
asleep. The task runs in the signed-in user's session with ordinary user rights.
Use `-NodePath` to select a specific node.exe and `-DataDirectory` to point a
separate release copy to the same private profile and state. Registration is
create-only by default. For an upgrade, supply `-PreviousRelease` plus the
existing `-NodePath` and `-DataDirectory`. Preview it with `-WhatIf`. The script
checks the registered owner, action and schedule before switching the launcher.
For rollback, use the same script with `-PreviousRelease` pointing to the current
package and `-TargetRelease` pointing to its predecessor. Windows Task Scheduler
also supports disabling or removing the task.

The default state directory is `data/rig-preparation` in the checkout. The
optional private `profile.json` in that directory accepts these fields:

| Field | Selection |
| --- | --- |
| `steamManifestPath` | Absolute path to `appmanifest_1066890.acf`. If omitted, the observer looks for a unique AMS2 manifest across local Steam libraries. |
| `expectedMozaDevices` | Array of objects with a human-readable `label` and exact Windows `instanceId`. Use the verified hardware endpoint. Virtual MOZA drivers can remain installed with the base off. |
| `controllerProfilePath` | Absolute path to the driver's active AMS2 saved controller profile. |
| `minimumIdleSeconds` | Defaults to 300. Both activity checks must meet the threshold. |

`--data-dir DIR` and `--profile FILE` select other locations. Changes to selected
device identities or file paths start a separate baseline. A missing selection
shows as unknown. Keep rig profiles and generated state in a private directory.

If any recognized racing simulator is open, recent input is detected, or Windows
cannot establish idle activity, the agent defers idle maintenance. When AMS2
is running, the scheduled agent can separately sample its shared memory for ten
seconds without prompts. It checks activity before and
after collecting evidence and discards the in-flight results if the driver
returns. One process at a time can write to the private state directory.

## Reading and acknowledging findings

`node preflight.mjs status` shows when the agent last tried and when it last
completed the checks. `--json` includes the exact findings. A completed check
may still contain a blocker or an unknown. The Rig Engineer can submit a silent
Windows Notification Center alert for a newly detected actionable problem.
Windows controls its availability and display. An expected disconnected base by
itself stays in the status report. The monitor checks state locally and uses no
model inference.

After a profile change, verify steering and pedal response during an ordinary
session. Use `node preflight.mjs accept-controls --fingerprint HASH` with the
exact hash in the report. The command verifies the saved bytes again. This
records the driver's acceptance while preserving the earlier finding.

## Checking steering and pedals

At an ordinary visit, park clear of traffic, in neutral in a practice or test
session. Select your own live cockpit with AI driving off. This foreground
command confirms those conditions, starts a guided check, and requests backup
enrollment for a successful result:

```powershell
node preflight.mjs check-inputs --parked-driver --enable-repair
```

Return focus to the cockpit. After five seconds of qualifying telemetry, one
beep asks you to turn the wheel, two ask for throttle, and three ask for brake.
For each cue, move just that control, hold briefly, then release or center it.
With `--text-only` the same instructions appear in the terminal. A change of
view, movement out of stationary neutral, stale frames, or loss of the game
process interrupts the check. Use `check-inputs` alone for silent observation.

If the configured rig has one device, its label is tried as an exact Windows
controller product name. To select a different device, run `list-controllers`
and pass its current `--controller-id SLOT` to the parked command. The report
distinguishes movement in AMS2, a driver-confirmed gesture, and movement timed
against a Windows axis. It can flag a Windows pedal movement with no matching
AMS2 response, or an unexpected AMS2 channel responding to the gesture. Separate
USB pedal sets can be checked in the same run with `--pedal-controller-id SLOT`.
The wheel and pedals can each have their own Windows X axis. WinMM reports
generic axis names. One observation cannot prove calibration, force feedback,
or that identical devices were never exchanged.

Automatic sampling reports what changed on the viewed car. Player attribution
requires the driver-confirmed check. A short passive window without braking can
leave brake response unknown. `status` retains the time of the latest observation
and the last successful driver-confirmed check separately. Recheck when the rig
changes.

## Recovering missing bindings

The first idle fingerprint is a comparison point. To authorize a recovery
copy, use the guided command above after an initial preflight, or run
`accept-controls --fingerprint HASH --enable-repair` after personally verifying
the bindings. The exact hash is available from `status --json` in
`lastCompleted.result.controls.fingerprint`. Omit `--enable-repair` to record
acceptance alone. `disable-repair` turns off future restores.

On a later idle cycle, if the configured file is missing, the agent verifies the
accepted bytes, path and parent directory, rechecks idle activity and publishes
the file with an atomic create-if-absent operation. A concurrently created file
takes precedence. The agent reads back the restored bytes before reporting
success. Existing altered files remain visible for review. A journal records the
attempt across restarts. A verified receipt, the latest attempt and its reason
appear in local status. Restoration verifies bytes on disk; the next cockpit
check verifies their behavior. Hardware power, firmware, Windows drivers, Steam
updates and calibration remain under their existing controls.

## Durable evidence

Each selected profile has an atomically replaced JSON state file for the latest
attempt, last completed observation and comparison baseline. A separate SQLite
store uses Pi Durable 1.5.2 and the project's SAS v0.2.0 definitions.
The Chief owns one Rig Engineer thread in this store. A deterministic handler
accepts the fixed request `{"review":"last_idle_preflight"}` and returns the
selected cycle's bounded fields. `{"review":"last_rig_inputs"}` reviews a
separately scoped input observation. Child result and explicit parent report commit
together. Restart reconciles the last saved cycle and deduplicates repeated
delivery. The local Pi host stores reports for the Chief; automatic activation of
a separate chat or model session is outside this implementation.

Run `node --test` in `agents/rig-preparation` to exercise idle deferral, Steam
manifest interpretation, device observations, profile drift, alert deduplication,
Pi reopen, and the normal CLI. Use the parked check above for live pedal input.
SimHub effects and menu navigation require separate arrival checks. For AMS2
shared-memory setup, see the [live simulator validation guide](live-simulator-validation.md).
