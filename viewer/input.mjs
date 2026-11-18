import assert from 'node:assert/strict';

function samples(values, count, predicate, label) {
  assert(Array.isArray(values) && values.length === count && values.every(v => v === null || predicate(v)),
    `${label} must have one finite sample or null per Lap Progress point`);
}

export function validateInput(data, timing = null) {
  assert(data?.format === 'orc_visual_preview' && data.version === 1, 'Expected orc_visual_preview version 1');
  for (const key of ['recorded_session_id', 'sim', 'track', 'car']) {
    assert(typeof data[key] === 'string' && data[key].trim(), `Missing ${key}`);
  }
  const { progress, laps, path, segments = [], analysis } = data;
  assert(Array.isArray(progress) && progress.length >= 2 && progress[0] === 0 && progress.at(-1) === 1 &&
    progress.every((v, i) => Number.isFinite(v) && (i === 0 || v > progress[i - 1])), 'Lap Progress must increase from 0 to 1');
  assert(['reportable', 'consistent', 'inconsistent', 'insufficient_data', 'no_single_dominant_issue'].includes(analysis?.status), 'Unknown Analysis Run status');
  assert(Array.isArray(laps) && laps.every(l => l && typeof l.valid === 'boolean' &&
    Number.isInteger(l.lap) && l.lap > 0 && l.lap_id === `${data.recorded_session_id}:lap:${l.lap}` &&
    Number.isFinite(l.lap_time_s) && l.lap_time_s > 0 && ['sim', 'derived_from_ticks'].includes(l.lap_time_source)), 'Invalid lap identity, validity, or timing');
  assert(new Set(laps.map(l => l.lap_id)).size === laps.length, 'Duplicate Lap IDs');
  const valid = laps.filter(l => l.valid);
  assert(valid.length >= 2 && valid.some(l => l.lap_id === analysis.reference_lap_id), 'A same-session Reference Lap and at least one Comparison Lap are required');
  assert.equal(analysis.comparison_lap_count, valid.length - 1, 'Comparison Lap count does not match the export');
  for (const lap of valid) {
    for (const name of ['speed', 'brake', 'throttle']) {
      samples(lap.signals?.[name], progress.length, v => Number.isFinite(v) && v >= 0 && (name === 'speed' || v <= 1), name);
    }
  }
  for (const axis of ['x', 'y']) samples(path?.[axis], progress.length, Number.isFinite, `path.${axis}`);
  assert(Array.isArray(segments) && segments.every(s => /^C[1-9]\d*$/.test(s?.id) &&
    Number.isFinite(s.start) && Number.isFinite(s.end) && 0 <= s.start && s.start < s.end && s.end <= 1), 'Invalid Corner Segment');
  assert(new Set(segments.map(s => s.id)).size === segments.length, 'Duplicate Corner Segment ID');
  if (timing !== null) {
    assert.equal(timing.recorded_session_id, data.recorded_session_id, 'Timing Recorded Session mismatch');
    assert.equal(timing.reference_lap_id, analysis.reference_lap_id, 'Timing Reference Lap mismatch');
    assert.deepEqual(timing.progress, progress, 'Timing Lap Progress mismatch');
    assert.equal(timing.units?.elapsed_s, 's', 'Elapsed timing must be in seconds');
    assert(timing.transformations && timing.limitations && timing.validation_provenance, 'Timing provenance is required');
    assert(Array.isArray(timing.laps), 'Missing elapsed timing lap series');
    assert.deepEqual(timing.laps.map(l => l.lap_id).sort(), valid.map(l => l.lap_id).sort(), 'Elapsed timing must cover the same valid Lap IDs');
    for (const lap of timing.laps) {
      assert.equal(lap.lap, valid.find(l => l.lap_id === lap.lap_id).lap, 'Elapsed timing lap number mismatch');
      let previous = -Infinity;
      samples(lap.elapsed_s, progress.length, v => {
        if (!Number.isFinite(v) || v < 0 || v <= previous) return false;
        previous = v;
        return true;
      }, 'elapsed_s');
    }
  }
}
