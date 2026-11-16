import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Script } from 'node:vm';
import { buildViewer } from '../viewer/build.mjs';
import { syntheticDemo } from '../viewer/demo.mjs';

test('builds self-contained HTML without mutating the display export', () => {
  const { data, timing } = syntheticDemo();
  const snapshot = structuredClone({ data, timing });
  const html = buildViewer(data, timing);
  assert.match(html, /data-bezel="off"/);
  assert.match(html, /default-src 'none'/);
  assert.doesNotMatch(html, /<script[^>]+src=|<link[^>]+href=|\bfetch\(|\bWebSocket\b|\bXMLHttpRequest\b|__CRT_PRESETS__/);
  new Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
  assert.deepEqual({ data, timing }, snapshot);
  assert.match(buildViewer(data), /id="orc-viewer-timing">null<\/script>/);
});

test('embeds telemetry as inert JSON including template markers and tag-like data', () => {
  const { data } = syntheticDemo();
  data.track = '</script><img src="sample"> {{VIEWER}}';
  const html = buildViewer(data);
  assert.doesNotMatch(html, /<img src="sample">/);
  assert.deepEqual(JSON.parse(html.match(/id="orc-viewer-data">([\s\S]*?)<\/script>/)[1]), data);
});

test('rejects mismatched lap evidence and invalid samples', () => {
  const { data, timing } = syntheticDemo();
  for (const [change, error] of [
    [d => { d.version++; }, /version/],
    [d => { d.analysis.reference_lap_id = d.laps.at(-1).lap_id; }, /Reference Lap/],
    [d => { d.laps[1].lap_id = d.laps[0].lap_id; }, /lap identity/],
    [d => { d.laps[1].signals.brake[0] = 1.1; }, /brake/],
    [d => { d.progress[20] = d.progress[19]; }, /Lap Progress/],
  ]) {
    const invalid = structuredClone(data);
    change(invalid);
    assert.throws(() => buildViewer(invalid), error);
  }
  for (const [change, error] of [
    [t => { t.recorded_session_id = 'other-demo'; }, /Recorded Session mismatch/],
    [t => { t.reference_lap_id = 'other-reference'; }, /Reference Lap mismatch/],
    [t => { t.progress[20] = .01; }, /Lap Progress mismatch/],
    [t => { t.laps[1].elapsed_s[120] = .1; }, /elapsed_s/],
  ]) {
    const invalid = structuredClone(timing);
    change(invalid);
    assert.throws(() => buildViewer(data, invalid), error);
  }
});
