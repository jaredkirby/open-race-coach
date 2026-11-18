# Input observation boundary

Status: actual read-only observer evidence plus proposed integration requirements.
Confidence is high in the scoped observation below; a live recipe adapter remains
unimplemented. This does not change the synthetic-only input boundary.

## Observed capability and limitation

A bounded inspection of an already-open rig application used the authorized
Window2 API: list windows, select exactly one returned window, rehydrate it and
read accessibility state, followed by one screenshot observation. No activation,
input, launch or configuration change was performed.

The accessibility result contained window chrome rather than the application's
device controls. The screenshot returned for the selected window contained
overlapping unrelated application content. Its app-isolation review failed; no
device state or control selector was inferred from it. This is one observed case,
not proof that every capture behaves this way. The underlying cause is unknown.
The mixed screenshot was not copied into the repository or retained artifact set;
private metadata records the failure and the scoped tool calls.

| Needed by ORC | Returned or observed at this boundary | Admission consequence |
| --- | --- | --- |
| Selected app/window reference | Returned app identifier, opaque window ID and title | Carry the returned object; do not invent a handle or interpret the opaque ID as a native HWND. |
| Native process lifetime | Not supplied by this Window2 state shape | PID and creation identity remain unknown until independently and correctly bound to that window. |
| Foreground identity | Accessibility focused-element text refers to the queried window | It does not establish that the app owns desktop foreground input. |
| Isolated app pixels | Actual selected-window capture included unrelated overlapping content | Reject as isolated app evidence; a selected-window request alone is insufficient. |
| Semantic controls | Only window chrome appeared in the sampled accessibility tree | No device/profile readiness or recipe selector is established. |
| Modal/driving/session state | Not established by these generic metadata fields | Keep unknown; do not fill false/default values to satisfy command validation. |
| App version | A title can contain a version string | Keep title-derived information separate from verified executable/version identity. |
| Observation time | Host-side request timestamps and elapsed duration | Record clock domain and provenance; do not substitute a foreign process's performance timestamp for the coordinator clock. |

The current recipe parser rejects the raw observer artifact. Renaming its fields
or changing its scope to synthetic is not an adapter and would hide missing proof.
An actual observation must never enter the test backend as supposed live validation.

## Required next adapter contract

1. The local owner issues a fresh observation request bound to its epoch, a nonce,
   selected target and a deadline measured on its own monotonic clock. Receipt of
   a response bounds request/return time without assuming shared clock origins.
   Interrupted, late or mismatched responses remain unusable.
2. Preserve the selected provider object and identify the captured scope. Explicitly
   record occlusion, related transient windows and any unrelated content. Unknown
   isolation is not a successful observation. Do not save unrelated desktop pixels
   just because a capture method returned them.
3. Bind native process lifetime and actual foreground ownership through a supported
   mechanism. An installed app name, similar window title or single matching process
   is not sufficient to associate an opaque provider window ID with native input.
4. Keep observations separate from interpretations. A semantic state/context claim
   records its evidence reference, method, confidence and unknown fields. Controls
   require unique current resolution; retained screenshots, indexes and coordinates
   are evidence, not durable selectors or permission to send input later.
5. Separate slower model interpretation from the final local check. The input path
   must recheck current target, focus, modality, generation, cancellation and the
   interpreted basis before dispatch. Do not loosen freshness merely to fit model
   latency or assume a late model answer still describes the current screen.
6. Preserve incomplete and rejected observations, including capture-scope failures,
   without manufacturing a complete command. Record the reason and the next needed
   evidence. A failure changes the preparation plan; it is not a reason to repeat
   user driving exercises.

The live provider bridge remains unimplemented and grants no new desktop access.
A private metadata-exchange pilot now binds request nonce, selected provider
window, owner generation and an owner-clock deadline. It retains rejected replies,
refuses mismatched or late responses, and checks generation/freshness again before
recipe compilation. Provider timestamps cannot refresh an old observation. Cached
acceptance is revoked after expiry or takeover; delayed journal publication is
included in the final check. Synthetic packets exercise the existing recipe,
gateway and coordinator path. Actual read-only observations cannot be promoted to
synthetic commands, even if a reply asserts native identity. A replacement exchange
instance does not resume pending requests. This is not a deployed transport, native
identity verifier, screenshot recognizer or full SAS adapter.

The synthetic executor now checks the basis again at the actual effect boundary,
after dispatch is journaled. Delayed callbacks cannot use an expired command or
changed fake target. This tests the required separation of durable admission from
current execution conditions; it does not resolve the actual observer gaps above.
While using the bundled Windows computer-use skill, all UI observations/actions
must continue through its supported API, returned-window selection and fresh
observe/action sequence. Do not bypass a missing capability with a private helper
protocol, raw input injection or another UI automation path.

## Validation and user experience

Before a live executor is enabled, replay normal and occluded windows, weak or
missing accessibility, changed process/window/focus, unexpected modals, delayed
responses, partial capture and human takeover. Verify that unavailable evidence
causes no input. An isolated screenshot still does not prove semantic recognition,
correct car/track/fuel, app/device readiness or actual command delivery.

Use a normal loading visit for the smallest remaining off-track observation, under
the user's existing input handoff. Keep recorder/fuel services independent. Do not
launch the game or request another driving acceptance sequence solely to make an
observer test pass. Measure end-to-end preparation and actual driver delay; a single
capture's elapsed duration is neither a responsiveness guarantee nor a speedup.

See [input orchestration](input-orchestration.md), [recipe contract](../schema/session_recipe.md)
and [returning to the rig](returning-to-the-rig.md).

Read-only application data interfaces can supply some readiness facts without
menu navigation. The [rig interface investigation](rig-observation-interfaces.md)
records the SimHub HTTP pilot and the unverified MOZA SDK path. These interfaces
do not resolve desktop focus, semantic control selectors or physical input ownership.
