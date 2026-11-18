// Entirely invented data. This is not a Recorded Session or live validation evidence.
export function syntheticDemo() {
  const recorded_session_id = 'synthetic-viewer-demo';
  const progress = Array.from({ length: 257 }, (_, i) => i / 256);
  const laps = [1, 2, 3, 4].map((lap, i) => ({
    lap, lap_id: `${recorded_session_id}:lap:${lap}`, valid: i !== 3,
    invalid_reason: i === 3 ? 'metadata_boundary' : null,
    lap_time_s: [59, 60.25, 61.625, 12][i], lap_time_source: 'derived_from_ticks',
    ...(i === 3 ? {} : { signals: {
      speed: progress.map(p => p === .5 ? null : 48 - 12 * Math.cos(6 * Math.PI * p + i / 6)),
      brake: progress.map(p => Math.max(0, .85 * Math.cos(6 * Math.PI * p + i / 6))),
      throttle: progress.map(p => .6 - .36 * Math.cos(6 * Math.PI * p + i / 6)),
    } }),
  }));
  const reference_lap_id = laps[0].lap_id;
  const data = {
    format: 'orc_visual_preview', version: 1, recorded_session_id,
    track: 'Synthetic Test Circuit', car: 'Demo Car', sim: 'synthetic',
    scope_note: 'Invented display fixture. No simulator recording or validation evidence.',
    progress, laps,
    analysis: { status: 'consistent', reference_lap_id, comparison_lap_count: 2, coaching_instruction: null },
    path: { x: progress.map(p => 165 * Math.cos(2 * Math.PI * p)), y: progress.map(p => 100 * Math.sin(2 * Math.PI * p)) },
    segments: [{ id: 'C1', start: .125, end: .25 }, { id: 'C2', start: .625, end: .75 }],
  };
  const timing = {
    recorded_session_id, reference_lap_id, progress, units: { elapsed_s: 's' },
    transformations: { origin: 'Invented elapsed time from synthetic lap start' },
    limitations: ['Demo only'], validation_provenance: { synthetic: true },
    laps: laps.filter(l => l.valid).map(({ lap, lap_id, lap_time_s }) => ({
      lap, lap_id, elapsed_s: progress.map(p => p === .5 ? null : lap_time_s * p + (lap - 1) * .3 * Math.sin(2 * Math.PI * p)),
    })),
  };
  return { data, timing };
}
