# Read-only rig observation interfaces

Status: a private SimHub observer and readiness integration are tested with
synthetic data and native Windows/HTTP fixtures. Confidence is high for those
scoped results. The actual installation diagnostic found no listener at the
configured HTTP port, so live SimHub payload validation remains NOT RUN. No app,
SDK, plugin, device output or settings change was performed.

## SimHub connection evidence

The inspected SimHub distribution's `Web/diag.html` and `Web/Maps/map.html`
use the existing `/Api/GetGameData` endpoint. Its installed `GameReaderCommon`
public metadata includes `FrameTimeUTC`, game-state flags and normalized car/track
fields. File hashes are retained privately; an executable file version of
`1.0.0.0` was not treated as a reliable product release identifier. Metadata and
shipped client usage establish an interface candidate, not live callback timing.

The observer uses the configured port and an explicit expected executable. Before
and after one fixed loopback GET, it checks the Windows listener owner, process
creation time and executable image. Missing, ambiguous, changed or inaccessible
ownership prevents a successful observation. It uses no proxy, redirects,
credentials, startup command or API that changes state. Responses have bounded
size and a total HTTP deadline, including slow header/body delivery.

The parser retains only game flags, provider UTC frame time and normalized context;
it does not retain raw payloads, driver/opponent names, paths, positions or lap
history. Response hashes, sizes, projected observations and failures remain in
private evidence. It requires fresh advancing provider timestamps and refuses
menus, pause, replay, spectating, wrong games and missing fields. Provider timestamp
semantics and the actual serialized shape still need observation during an ordinary
visit; no timing guarantee follows from the synthetic HTTP server.

The private readiness integration requires an explicit reviewed mapping for
SimHub's executable, game, car, track, layout and session labels. It does not guess
those labels from AMS2 enums. Expiry participates in the combined readiness deadline;
a synthetic provider cannot satisfy an actual-source assessment. This can satisfy
only the SimHub game-data gate. Profiles, physical haptics, MOZA devices, radio
hearing and session setup retain their independent evidence requirements.

Windows ownership checks use documented
[TCP endpoint inspection](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable)
and [process image queries](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-queryfullprocessimagenamew).
They are point-in-time observations, not an atomic ownership lock or protection
against a hostile same-account process. The reader does not enable a listener or
change its binding/firewall configuration. An absent listener is not proof that
the application is absent.

## Boundaries for further preparation

| Interface | Appropriate use | Unresolved boundary |
| --- | --- | --- |
| SimHub game-data HTTP | Read reported connection and normalized context from the existing local endpoint. | Actual payload/timestamp validation; no profile or physical-output proof. |
| SimHub plugin SDK | Candidate if the existing endpoint cannot provide the necessary evidence. | Requires separate build, failure/performance testing and deployment review; no plugin is installed for this pilot. |
| SimHub command line | Bounded preparation actions under the input coordinator. | A command's exit code does not prove the requested state was achieved. |
| MOZA SDK | Pinned device-name query contract inspected; offline response interpretation tested. | Initialization effects, installed-build compatibility and actual native reads are unverified; see the [device observation boundary](moza-device-observation.md). |
| Computer-use observation | Fresh semantic menu/device evidence through the supported provider. | Native window/focus binding and capture isolation remain separate from application data APIs. |

MOZA describes discovery through its SDK-specific Pit House integration; it also
exposes parameter writes, calibration and force feedback. Those capabilities are
outside this read-only investigation. Availability of the SDK does not establish
compatibility with the installed application. [MOZA SDK](https://mozaracing.com/pages/sdk).
SimHub's documented [command-line interface](https://github.com/SHWotever/SimHub/wiki/Command-line-interface)
is a control interface, not a substitute for observation receipts.

These are deterministic evidence adapters. A future SAS tool should expose a
bounded read operation and a typed result, while the Chief retains interpretation
and the local coordinator retains action authority. The private pilot is neither
a deployed specialist nor a SAS-conformance claim. See
[readiness assessment](chief-readiness-assessment.md) and
[input observation boundary](input-observation-boundary.md).
