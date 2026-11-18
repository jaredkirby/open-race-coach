# Open Race Coach

Open Race Coach is a local-first sim-racing project evolving toward a personal race chief that owns rig readiness, driving-time protection, practice coordination and feedback. Its implemented post-session coaching core turns recorded telemetry into at most one data-supported Coaching Instruction per Analysis Run.

[Race-chief direction](docs/race-chief-direction.md) separates accepted product requirements from proposed implementation.
Private durable-specialist pilots exist; general deployment, reboot recovery
and validated in-car radio remain open.
Local fuel-supervision and audio-rendering/dispatch prototypes are documented
separately; offline execution is not evidence of a complete live Race Chief.
Existing Recorded Session and analysis contracts remain authoritative.

## Language

**Race Chief**:
The principal agent responsible for the driver's goals, team coordination, priorities, decisions and follow-through. This is the accepted product role; an autonomous durable deployment is future work.
_Avoid_: Making the driver manage specialist chats or reconcile conflicting instructions.

**Driver Visit**:
An explicitly bounded period when the driver is using the rig. Simulator activity alone does not establish physical presence. Driving, pit movement, rest, waiting and unknown time must remain distinguishable. The standalone driver clock lacks reason annotations; the experimental visit ledger records declared reasons separately from observed activity.
_Avoid_: Treating all non-driving time as wasted time or a running simulator as proof of occupancy.

**Input Coordinator**:
The proposed ORC-owned local component that admits and serializes application/menu actions, verifies fresh targets, yields control to the driver and records ambiguous outcomes. Agent composition uses Standard Agent Specification where appropriate; physical input ownership is a separate ORC contract. It is not an installed desktop controller or an automated driving system.
_Avoid_: Treating a durable agent identity or completed tool call as proof that a physical action was authorized or achieved.

**Live Reaction**:
A proposed brief acknowledgment of a sufficiently supported event, delivered while still relevant and appropriate to the driver's workload. It is distinct from a causal diagnosis and from a Coaching Instruction.
_Avoid_: Calling a slowdown a spin without evidence, or presenting a latency target as measured performance.

**Radio Transmission**:
A proposed driver-facing audio message with evidence, priority, validity and expiry, rendered through a real audio-processing chain. Generated, queued, played and acknowledged are distinct states; playback does not prove understanding.
_Avoid_: Treating chat output as delivered audio or allowing independent specialists to speak over one another.

**Recorded Session**:
The immutable analysis unit captured by Open Race Coach. A Recorded Session is homogeneous for sim, track, car, session type, adapter version, and artifact schema versions; pit stops, pauses, slow laps, invalid laps, and multiple stints may remain inside it as long as that context does not change. An observed lap counter regression also starts a new Recorded Session to keep distinct counter histories separate; it does not establish that the simulator restarted.
_Avoid_: Session when the meaning could be confused with a simulator session, stint, lap, or filesystem directory.

In the live Driver Visit prototype, a grace-expired source gap or an observed
source-sequence epoch change or observed active viewed-slot change also separates
Recorded Session identities. A
supported prefix stays immutable; resumed observations start a new history.
These are continuity boundaries, not proof that the simulator restarted.

**Recorded Session ID**:
A stable generated identifier for a Recorded Session. Paths locate a Recorded Session on disk, but the Recorded Session ID identifies it across moves or copies.
_Avoid_: Session path when identity is required.

**Incomplete Recorded Session**:
A Recorded Session whose capture or finalization failed before all required artifacts were safely written. It is retained for audit or debugging but is not eligible for analysis.
_Avoid_: Partial session when eligibility for analysis matters.

**Live Simulator Validation**:
The first evidence-gathering activity that proves Open Race Coach can observe, preserve, validate, and analyze a real simulator session from a configured rig. Live Simulator Validation establishes telemetry trust before evaluating coaching quality.
_Avoid_: Rig test, playtest, coaching evaluation.

**Minimal Clean-Lap Live Simulator Validation**:
A Live Simulator Validation scenario using one stable simulator, track, car, and practice context to produce a short Recorded Session with clean timed laps and no intentional pauses, pits, replay, restarts, or setup/context changes. It exists to prove telemetry artifact trust before realistic stint behavior is tested.
_Avoid_: Stint test, race simulation, coaching-quality test.

**Recorded Session Boundary**:
The point where Open Race Coach must finalize one Recorded Session and start another because the recorded context changes or an observed lap counter regression makes continued lap identity ambiguous. The latter is a counter discontinuity, not a confirmed simulator restart.
_Avoid_: Split when the domain event is a change in recording context.

**Simulator Label**:
The raw track or car name reported by the simulator for display and audit. Open Race Coach stores normalized matching keys separately.
_Avoid_: Name when matching semantics are being discussed.

**Matching Key**:
A deterministic normalized value used to group Recorded Sessions for exact-match features such as Personal Best Reference selection.
_Avoid_: Alias, display name.

**Artifact Schema Version**:
The version of one durable Open Race Coach artifact contract, such as session metadata, ticks, lap records, or Analysis Run outputs.
_Avoid_: Global schema version.

**Capture Time**:
The monotonic elapsed time stored on telemetry ticks, measured in seconds from the start of capture. Capture Time is not a simulator clock value and must not move backward or freeze while Open Race Coach is recording.
_Avoid_: Sim time when referring to the normalized `t` field.

**Lap**:
One complete traversal of the track within a Recorded Session, as identified from simulator lap state and validated by Open Race Coach.
_Avoid_: Run, attempt.

**Lap ID**:
A stable identifier for a Lap, formed from the Recorded Session ID and lap number.
_Avoid_: Lap number when referring across Recorded Sessions.

**Lap Progress**:
The normalized position of a car within a Lap, represented by lap-distance percentage. Lap Progress is the alignment basis for comparing laps.
_Avoid_: Distance when the value is not measured in meters.

**Lap Distance Source**:
The provenance of meter-based lap distance in a Recorded Session: simulator-provided, derived from track length, or unavailable.
_Avoid_: Distance accuracy when the issue is provenance.

**Vehicle State**:
The normalized capture state of the car at a telemetry tick, such as running, pit, paused, menu, replay, or unknown. Vehicle State protects lap validity from treating non-driving telemetry as normal driving evidence.
_Avoid_: Session state when the value describes the car's capture state at a tick.

**Valid Lap**:
A Lap eligible for Reference Lap selection and coaching delta aggregation. Slow laps are valid unless there is concrete invalidity evidence; invalid laps are stored for auditability but excluded from coaching evidence.
_Avoid_: Clean lap when the question is eligibility for analysis.

**Validity Method**:
The Recorded Session-level provenance for Valid Lap decisions: simulator invalidation flags plus inferred checks, inferred checks alone, or inferred checks under unknown Vehicle State constraints.
_Avoid_: Cleanliness method.

**Reference Lap**:
A valid Lap selected as the comparison baseline for coaching analysis. It may come from the same Recorded Session or from prior Recorded Sessions with the same sim, track, and car.
_Avoid_: Target lap, optimal lap, theoretical lap.

**Reference Mode**:
The Reference Lap selection rule used for an Analysis Run: session best (`best`) or Personal Best Reference (`personal`).
_Avoid_: Reference source when discussing the selection rule rather than the selected lap's storage location.

**Corner Segment**:
A numbered analysis segment derived from the Reference Lap's path geometry. A Corner Segment is not an official circuit corner name or official turn number.
_Avoid_: T1, T2, official corner, named corner.

**Personal Best Reference**:
The fastest Valid Lap found across stored Recorded Sessions with the same sim, track, and car, including the Recorded Session currently being analyzed. When the selected lap comes from the current Recorded Session, it is also the session best.
_Avoid_: Historical best when current-session laps are eligible.

**Analysis Run**:
One execution of Open Race Coach analysis for a Recorded Session using a specific Reference Lap selection and confidence policy.
_Avoid_: Session analysis, report run.

**Comparison Lap**:
A Valid Lap from the Recorded Session being analyzed that is compared against the Reference Lap. The Reference Lap itself is excluded when it comes from the same Recorded Session.
_Avoid_: Sample lap, candidate lap.

**Reportable Delta**:
A data-supported difference between Comparison Laps and the Reference Lap that is large enough, repeatable enough, and specific enough to support a Coaching Instruction.
_Avoid_: Finding, insight, anomaly.

**Lap Loss Cause**:
The primary driver-input difference assigned to a Comparison Lap within a Corner Segment when that lap loses meaningful time to the Reference Lap.
_Avoid_: Mistake, error, problem.

**Coach Report**:
The human-readable output of an Analysis Run. A Coach Report gives at most one Coaching Instruction and must identify the Reference Lap selection it used.
_Avoid_: Session report when the reference selection matters.

**Debrief Board**:
A driver-facing composition of Coaching Tiles arranged for immediate post-session reading. A Debrief Board is a presentation of coaching outputs and notes, not a new source of analysis truth.
_Avoid_: Dashboard, terminal editor, report builder.

**Debrief Layout Preset**:
A named arrangement of Coaching Tiles selected for a specific post-session reading pattern. Debrief Layout Presets constrain composition so the board stays driver-facing instead of becoming a generic canvas.
_Avoid_: Freeform layout, window manager.

**Coaching Tile**:
A typed block on a Debrief Board with a specific coaching purpose, such as instruction, evidence, reference context, checked areas, session status, or raw note. A Coaching Tile may be edited as text, but its type defines what role it plays in the debrief.
_Avoid_: Widget, panel, generic text box.

**ASCII Tile Format**:
The driver-facing text presentation pattern applied by a Coaching Tile type. The format provides the tile's frame, label, and density while leaving the tile's coaching content editable.
_Avoid_: Theme when referring to the structure of an individual tile.

**Tile Source Snapshot**:
The coaching-system content a Coaching Tile was derived from at the time it was created or refreshed. It preserves the tile's evidence basis separately from the editable display text shown on the Debrief Board.
_Avoid_: Live source when the tile is not automatically updated.

**Tile Display Text**:
The editable ASCII text shown inside a Coaching Tile on a Debrief Board. Tile Display Text may diverge from its Tile Source Snapshot when a user rewrites the debrief presentation.
_Avoid_: Source data, analysis output.

**Edited Coaching Tile**:
A source-derived Coaching Tile whose Tile Display Text no longer matches its Tile Source Snapshot. An Edited Coaching Tile remains usable in a Debrief Board, but should be distinguishable from an unmodified derived tile.
_Avoid_: Invalid tile, dirty widget.

**Coach Refinement**:
An optional LLM-assisted rewrite of Coach Report prose after deterministic analysis has selected the status, Reference Lap, Corner Segment, and Reportable Delta evidence.
_Avoid_: Analysis when the model is only rewriting prose.

**Stint Checkpoint**:
A chief-requested recording boundary within an ongoing Driver Visit. The worker
records into a successor Recorded Session while one background job validates and
analyses the prefix. It does not declare departure, prove a pit return or establish
a completed simulator lap. Its output requires chief review.
_Avoid_: New visit, automatic pit detection, live validation.

**Coaching Instruction**:
One concrete, driver-facing action selected from data-supported differences between valid laps and the Reference Lap.
_Avoid_: Tip, advice, recommendation.
