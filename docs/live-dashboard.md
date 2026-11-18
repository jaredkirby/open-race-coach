# Private live dashboard prototype

The [guarded visit release](visit-releases.md) packages the page with its Python
bridge and records the selected source in the actual process receipt. Activation
and the private Mac route still require separate verification.

The separate live page shows current car/track/mode, speed, controls, engine,
fuel, lap counter and available simulator lap times. It leaves the existing
offline debrief viewer intact. Readings clear when evidence expires or the
connection is lost; the page distinguishes waiting, fresh, stale and disconnected.
It can also show an explicitly reviewed segment and a verified operations-service
state. Historical feedback and live readings remain visibly separate.

The Chief runs the read-only Windows bridge against one explicitly verified
archive, in a separate hidden process with a new private output directory:

```powershell
.venv/Scripts/python.exe -B -m simcoach.cli.dashboard `
  --archive "<exact-archive>" --out "<new-private-bridge-directory>" --duration 3600
```

The process binds only 127.0.0.1. Its private run.json records the selected port
and native identity; connection-code.txt supplies the page code. The Chief opens
the page at that loopback port and connects it. No driver terminal work is
intended. The code stays in page memory and is not written to a URL or browser
storage. A stop file in that bridge directory ends the bridge without touching
capture, clocks or radio. Use fresh evidence paths; do not overwrite failed runs.

Mac integration still needs a reviewed encrypted private route. The current
server intentionally accepts only localhost/127.0.0.1 Host origins, as used by
a local port forward; it is not ready for arbitrary reverse-proxy origins.
No tunnel, firewall opening, tailnet membership or public hosting is installed
by this command. The existing Mac viewer is offline and has no live receiver.
Mac route verification must include authentication, fresh/stale/disconnected
display, interruptions, clock/resume behavior and actual latency before release.
Chat coordination and an offline export do not meet that gate.

The bridge reads bounded archive metadata/status only. It sends a fixed scalar
allowlist, with explicit units and nulls for unavailable fields. It never serves
raw captures, participant names, paths, credentials or arbitrary archive files.
Two progressing source observations are required; polling an old sample does not
renew it. Source, context, viewed-slot changes and lap-counter resets clear the
display. The receiver rechecks those boundaries and the published source epoch
so a skipped response cannot join readings across an observed interruption. A visible
fuel amount does not establish fuel supervision, and a viewed car is not proof
of the local driver. See the [wire/artifact contract](../schema/live_dashboard.md).

In-flight responses are bound to the browser clock state at request time.
A detected clock change clears the display and discards outstanding old
responses. Two usable post-change observations are required before live
readings return. The browser tests hold a real reply across an injected clock
change and verify this behavior both directly and through the Windows
loopback tunnel. Physical sleep/resume remains a separate check.

New collector summaries add simulator last/best/current lap-time fields.
Collectors started before this change may lack them; the page shows unavailable
values rather than reconstructing timing or silently deploying a collector update.

Validation uses synthetic data only:

```powershell
.venv/Scripts/python.exe -B -m pytest tests/test_dashboard.py tests/test_activity_evidence.py -q
node tests/live_dashboard_test.mjs
node tests/live_dashboard_browser_test.mjs "<new-private-browser-evidence>"
```

The browser test uses a separate headless Chrome profile and a synthetic loopback
fixture, retains screenshots/logs, and closes the fixture. It needs local socket
access. Confidence is high for scoped synthetic HTTP, receiver and browser
behavior. Actual Mac transport, simulator impact, real data and ordinary-visit
acceptance remain NOT RUN.

A Windows transport rehearsal used the installed Git for Windows OpenSSH server
and a Windows OpenSSH client with disposable keys, exact host-key pinning and a
loopback-only forward. Chrome displayed advancing synthetic readings through the
tunnel, cleared them when the source became stale, and disconnected when the
tunnel stopped while the publisher stayed up. After a deliberate tunnel restart,
the page waited for new source progress before showing readings. Wrong host key,
wrong client key, wrong account, session requests, remote forwarding and one
alternate forwarding destination were refused. All test listeners closed.

This rehearsal verifies the local encrypted transport with the tested binaries.
The proposed Windows-serviced SSH instance, actual Mac client, LAN path, and
sleep/resume behavior need separate checks.

The Windows process integration check also starts the real loopback bridge beside
the visit host, recorder and operations service, with synthetic simulator input:

```powershell
.venv/Scripts/python.exe -B -m pytest tests/test_visit_operations_process.py -q
```

It verifies capture in the garage before departure, fuel display after explicit
approval and mode changes, and reviewed checkpoint feedback over HTTP while the
same visit and recorder continue. An operations-service crash must clear dashboard
fuel status while capture continues. Test logs retain checkpoint time, selection-to-
HTTP delay and request round trips; these are local observations, not Mac or audio
latency. No actual driver, simulator, headset or private network route is exercised.
Confidence is high for those bounded synthetic process checks; live acceptance
remains separate.

## Review a segment while the visit continues

After a checkpoint has finalized a Recorded Session, the Chief prepares a private
card. This validates saved artifacts and retains every lap, including slow valid
laps and exclusions. It neither reruns analysis nor ends the seated visit:

```powershell
.venv/Scripts/python.exe -B -m simcoach.cli.dashboard_feedback prepare `
  --visit "<visit>" --session "<finalized-recorded-session>" --target-laps 3 --target-spread 0.5
.venv/Scripts/python.exe -B -m simcoach.cli.visit review "<visit>" `
  --output "<printed-card-path>" --verdict useful --note "<explicit chief assessment>"
.venv/Scripts/python.exe -B -m simcoach.cli.dashboard_feedback select `
  --visit "<visit>" --review "<printed-review-path>"
```

The example target is a Chief-selected repeatability objective, not a technique
diagnosis or guaranteed result. Inspect the card before review. Select an
insufficient review to suppress its objective, or a misleading/failed review to
withhold it. Failed attempts and earlier selections remain private evidence.

Windows feedback preparation and selection use extended paths for filesystem I/O
while retaining ordinary locators in source bindings. Long recording paths do not
relax ownership, source hashing, review matching or publication requirements.

Start the bridge with `--visit <visit>` to follow that explicit selection without
restarting it after each review. A card covers one Recorded Session, not the whole
visit or every split in a stint. Its end time, review time and lap timing sources stay
visible. Captured and simulator-reported times are labeled separately. Mixed-source
cards show separate source statistics and withhold combined pace, spread and targets.
Version 1 cards lack per-lap source evidence: preserve them and prepare/review a new
version 2 card from the original recording before selecting it. No prior review is
silently transferred. No driver endorsement is inferred. An objective is shown only with fresh
matching car/track/mode; historical feedback remains labeled when telemetry is stale.

Use `--operations <exact-service-directory>` for fuel status. Only a freshly
verified operations service with matching native identity, source and context can
show armed/return-due. Unarmed, suspended and unverified remain distinct; stale
evidence clears active status. Armed and return-due require a fresh service sample
from the displayed source epoch, ordered against the displayed timestamp and
sequence. During transitions the panel can show unverified until the two readers
catch up. Existing embedded/legacy service output without
source binding remains unverified. This does not arm fuel, start audio or establish
audible delivery. The selected service and collector must be deployed/reviewed
separately. [Feedback contract](../schema/dashboard_feedback.md).
