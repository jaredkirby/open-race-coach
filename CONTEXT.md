# SIM-COACH

SIM-COACH is a local-first post-session coaching tool for sim racing. It turns recorded driving telemetry into one data-supported coaching instruction for the next session.

## Language

**Recorded Session**:
The immutable analysis unit captured by SIM-COACH. A Recorded Session is homogeneous for sim, track, car, session type, adapter version, and artifact schema versions; pit stops, pauses, slow laps, invalid laps, and multiple stints may remain inside it as long as that context does not change.
_Avoid_: Session when the meaning could be confused with a simulator session, stint, lap, or filesystem directory.

**Recorded Session ID**:
A stable generated identifier for a Recorded Session. Paths locate a Recorded Session on disk, but the Recorded Session ID identifies it across moves or copies.
_Avoid_: Session path when identity is required.

**Incomplete Recorded Session**:
A Recorded Session whose capture or finalization failed before all required artifacts were safely written. It is retained for audit or debugging but is not eligible for analysis.
_Avoid_: Partial session when eligibility for analysis matters.

**Recorded Session Boundary**:
The point where SIM-COACH must finalize one Recorded Session and start another because the recorded context changes.
_Avoid_: Split when the domain event is a change in recording context.

**Simulator Label**:
The raw track or car name reported by the simulator for display and audit. SIM-COACH stores normalized matching keys separately.
_Avoid_: Name when matching semantics are being discussed.

**Matching Key**:
A deterministic normalized value used to group Recorded Sessions for exact-match features such as Personal Best Reference selection.
_Avoid_: Alias, display name.

**Artifact Schema Version**:
The version of one durable SIM-COACH artifact contract, such as session metadata, ticks, lap records, or Analysis Run outputs.
_Avoid_: Global schema version.

**Lap**:
One complete traversal of the track within a Recorded Session, as identified from simulator lap state and validated by SIM-COACH.
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
One execution of SIM-COACH analysis for a Recorded Session using a specific Reference Lap selection and confidence policy.
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

**Coach Refinement**:
An optional LLM-assisted rewrite of Coach Report prose after deterministic analysis has selected the status, Reference Lap, Corner Segment, and Reportable Delta evidence.
_Avoid_: Analysis when the model is only rewriting prose.

**Coaching Instruction**:
One concrete, driver-facing action selected from data-supported differences between valid laps and the Reference Lap.
_Avoid_: Tip, advice, recommendation.
