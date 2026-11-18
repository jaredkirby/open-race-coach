import assert from "node:assert/strict";
import { LiveDisplay } from "../docs/live-dashboard.mjs";

const nonce = "0123456789abcdef", publisher = "a".repeat(32), source = "b".repeat(64);
const telemetry = { context: { car: "Synthetic", track: "Test", variation: "", session_state: 1 },
  view: { identity: "viewed_car_unconfirmed", index: null, count: null }, speed_mps: 20,
  rpm: 5000, gear: 3, throttle_fraction: .5, brake_fraction: 0, steering_fraction: 0,
  fuel_litres: 4, lap: 2, lap_distance_m: 50, lap_invalidated: false,
  simulator_last_lap_s: null, simulator_best_lap_s: null, simulator_current_lap_s: null };
const packet = (sequence, overrides = {}) => ({ schema_version: 1, publisher_id: publisher,
  request_id: nonce, state: "fresh", reason: null, frame: sequence, source_id: source,
  source_sequence: sequence * 2, source_samples: sequence * 60, source_at: 1000 + sequence,
  telemetry: structuredClone(telemetry), valid_for_ms: 2000, ...overrides });
function receive(display, data, at, rtt = 10) {
  const request = display.beginRequest(nonce, at - rtt, 100000 + at - rtt);
  display.receive(data, { ...request, received: at, receivedWall: 100000 + at });
  return display.view(at, 100000 + at);
}
function ready() {
  const d = new LiveDisplay();
  assert.equal(receive(d, packet(1), 100).state, "waiting");
  assert.equal(receive(d, packet(2), 1100).state, "fresh");
  return d;
}
let checks = 0;
{
  const d = ready(), deadline = d.deadline;
  assert.equal(receive(d, packet(2, { frame: 3 }), 1600).state, "fresh");
  assert.equal(d.deadline, deadline); // A new HTTP response cannot renew old data.
  assert.equal(d.view(deadline, 100000 + deadline).state, "stale");
  assert.equal(d.telemetry, null);
  assert.equal(d.view(4700, 104700).state, "disconnected"); checks++;
}
{
  const d = ready();
  const changed = packet(2); changed.telemetry.brake_fraction = 1;
  assert.equal(receive(d, changed, 1500).state, "disconnected");
  assert.equal(d.telemetry, null); checks++;
}
{
  const d = ready();
  assert.equal(receive(d, packet(3, { request_id: "stale-request" }), 2100).state, "disconnected");
  assert.equal(d.telemetry, null); checks++;
}
{
  const d = ready();
  assert.equal(receive(d, packet(3), 4100, 2500).state, "stale");
  assert.equal(d.telemetry, null); checks++;
}
{
  const d = ready();
  assert.equal(receive(d, packet(1, { publisher_id: "c".repeat(32) }), 1500).state, "waiting");
  assert.equal(d.telemetry, null);
  assert.equal(receive(d, packet(2, { publisher_id: "c".repeat(32) }), 2500).state, "fresh"); checks++;
}
{
  const d = ready();
  assert.equal(receive(d, packet(3, { state: "stale", telemetry: null }), 1500).state, "stale");
  assert.equal(d.telemetry, null);
  assert.equal(receive(d, packet(4), 2500).state, "waiting"); checks++;
}
for (const change of [
  data => { data.context.session_state = 5; },
  data => { data.view = { ...data.view, index: 1, count: 2 }; },
  data => { data.lap = 1; },
]) {
  const d = ready();
  const changed = packet(3, { operations: { state: "return_due", valid_for_ms: 1500,
    service_id: "c".repeat(32), updated_at: 100 } });
  change(changed.telemetry);
  const next = receive(d, changed, 2100);
  assert.equal(next.state, "waiting"); // Receiver missed the publisher's clearing packet.
  assert.equal(next.telemetry, null);
  assert.equal(next.operations.state, "unknown");
  assert.equal(receive(d, packet(4, { telemetry: changed.telemetry }), 2600).state, "fresh");
  checks++;
}
{
  const d = new LiveDisplay();
  receive(d, packet(1, { source_epoch: 0 }), 100);
  assert.equal(receive(d, packet(2, { source_epoch: 0 }), 1100).state, "fresh");
  assert.equal(receive(d, packet(5, { source_epoch: 1 }), 2600).state, "waiting");
  assert.equal(receive(d, packet(6, { source_epoch: 1 }), 3100).state, "fresh"); checks++;
}
{
  const d = ready();
  assert.equal(d.view(1200, 115000).state, "disconnected");
  assert.equal(d.telemetry, null); checks++;
}
{
  const d = ready();
  assert.equal(receive(d, packet(1), 1500).state, "waiting");
  assert.equal(d.telemetry, null);
  assert.equal(receive(d, packet(2), 2500).state, "fresh"); checks++;
}
for (const bad of [{ schema_version: 2 }, { valid_for_ms: Infinity }, { reason: {} },
  { telemetry: { ...telemetry, speed_mps: "20" } }, { telemetry: null },
  { telemetry: { ...telemetry, view: null } }, { source_epoch: -1 },
  { source_at: NaN }, { source_sequence: -1 }]) {
  const d = ready();
  assert.equal(receive(d, packet(3, bad), 2100).state, "disconnected");
  assert.equal(d.telemetry, null); checks++;
}
{
  const d = ready(), op = { state: "armed", valid_for_ms: 1000, service_id: "c".repeat(32), updated_at: 100 };
  assert.equal(receive(d, packet(3, { operations: op }), 2100).operations.state, "armed");
  assert.equal(receive(d, packet(3, { operations: op }), 2600).operations.state, "armed");
  assert.equal(d.view(3100, 103100).operations.state, "unknown"); checks++;
}
{
  const d = ready();
  const feedback = { state: "reviewed", review_id: "d".repeat(32), reviewed_at: 1000,
    evidence_mode: "replay", verdict: "useful", card: { timing_basis: "captured_time",
      scope: "one_recorded_session", context: { car: "Test", track: "Test", session_type: "practice" },
      ended_at: 900, laps: [{ lap: 1, lap_time_s: 50, lap_time_source: "derived_from_ticks", valid: true, invalid_reason: null }],
      timing_groups: { derived_from_ticks: { valid_laps: 1, best_s: 50, median_s: 50, spread_s: null } },
      statistics: { valid_laps: 1, total_lap_records: 1, best_s: 50, median_s: 50, spread_s: null }, objective: null } };
  assert.equal(receive(d, packet(3, { feedback }), 2100).feedback.review_id, feedback.review_id);
  assert.ok(receive(d, packet(4, { feedback, state: "stale", telemetry: null }), 2600).feedback);
  d.disconnect(); assert.equal(d.feedback, null);
  feedback.card.laps[0] = { lap: 1, lap_time_s: 50, valid: false, invalid_reason: null };
  assert.equal(receive(d, packet(5, { feedback }), 3100).feedback, null); checks++;
}
{
  const f = { state: "reviewed", review_id: "e".repeat(32), reviewed_at: 1000,
    evidence_mode: "replay", verdict: "useful", card: { timing_basis: "mixed",
      scope: "one_recorded_session", context: { car: "Test", track: "Test", session_type: "practice" },
      ended_at: 900, laps: [
        { lap: 1, lap_time_s: 50, lap_time_source: "sim", valid: true, invalid_reason: null },
        { lap: 2, lap_time_s: 55, lap_time_source: "derived_from_ticks", valid: true, invalid_reason: null }],
      statistics: { valid_laps: 2, total_lap_records: 2, best_s: null, median_s: null, spread_s: null },
      timing_groups: { sim: { valid_laps: 1, best_s: 50, median_s: 50, spread_s: null },
        derived_from_ticks: { valid_laps: 1, best_s: 55, median_s: 55, spread_s: null } }, objective: null } };
  assert.equal(receive(ready(), packet(3, { feedback: f }), 2100).feedback.card.timing_basis, "mixed"); checks++;
  for (const mutate of [
    card => card.timing_basis = "captured_time",
    card => card.statistics.spread_s = 5,
    card => delete card.laps[0].lap_time_source,
    card => card.objective = { laps: 3, spread_s: .5, timing_basis: "captured_time" },
  ]) {
    const changed = structuredClone(f); mutate(changed.card);
    assert.equal(receive(ready(), packet(3, { feedback: changed }), 2100).feedback, null); checks++;
  }
  const sim = structuredClone(f);
  sim.card.laps.forEach(lap => lap.lap_time_source = "sim");
  sim.card.timing_basis = "simulator_time";
  sim.card.statistics = { valid_laps: 2, total_lap_records: 2, best_s: 50, median_s: 52.5, spread_s: 5 };
  sim.card.timing_groups = { sim: sim.card.statistics };
  assert.equal(receive(ready(), packet(3, { feedback: sim }), 2100).feedback.card.timing_basis, "simulator_time"); checks++;
}
{
  const d = ready();
  const delayed = d.beginRequest(nonce, 1140, 101140);
  assert.equal(d.view(1200, 140000).state, "disconnected");
  d.receive(packet(3, { operations: { state: "return_due", valid_for_ms: 1500,
    service_id: "c".repeat(32), updated_at: 100 } }),
    { ...delayed, received: 1210, receivedWall: 140010 });
  assert.deepEqual(d.view(1210, 140010), { state: "disconnected", reason: "Clock changed; reconnecting",
    telemetry: null, feedback: null, operations: { state: "unknown" } });
  const first = d.beginRequest(nonce, 1600, 140400);
  d.receive(packet(4), { ...first, received: 1610, receivedWall: 140410 });
  assert.equal(d.view(1610, 140410).state, "waiting");
  const second = d.beginRequest(nonce, 2100, 140900);
  d.receive(packet(5), { ...second, received: 2110, receivedWall: 140910 });
  assert.equal(d.view(2110, 140910).state, "fresh"); checks++;
}
{
  const d = ready();
  const delayed = d.beginRequest(nonce, 1140, 101140);
  d.view(1200, 140000);
  d.view(1300, 101300);
  // The endpoints agree after a forward and backward wall-clock change.
  d.receive(packet(3), { ...delayed, received: 1330, receivedWall: 101330 });
  assert.equal(d.view(1330, 101330).state, "disconnected");
  assert.equal(receive(d, packet(4), 1800).state, "waiting"); checks++;
}
{
  const d = ready();
  assert.equal(d.view(1350, 101650).state, "fresh");
  assert.equal(d.view(1600, 102200).state, "disconnected");
  assert.equal(d.telemetry, null); checks++;
}
{
  const d = new LiveDisplay();
  assert.equal(receive(d, packet(1), 2500, 2300).state, "stale");
  assert.equal(receive(d, packet(2), 3000).state, "waiting");
  assert.equal(receive(d, packet(3), 3500).state, "fresh"); checks++;
}
{
  const d = ready();
  assert.equal(receive(d, packet(3), 4200).state, "waiting");
  assert.equal(receive(d, packet(4), 4700).state, "fresh"); checks++;
}
{
  const d = ready();
  const request = d.beginRequest(nonce, 1200, 101200);
  d.receive(packet(3, { valid_for_ms: 499 }),
    { ...request, received: 1300, receivedWall: 101700 });
  assert.equal(d.view(1300, 101700).state, "stale"); checks++;
}
{
  const d = ready(), request = d.beginRequest(nonce, 1140, 101140);
  // No paint callbacks run while the request is suspended.
  d.receive(packet(3), { ...request, received: 1210, receivedWall: 140010 });
  assert.equal(d.view(1210, 140010).state, "disconnected");
  assert.equal(d.telemetry, null); checks++;
}
console.log(`PASS ${checks} live dashboard receiver scenarios`);
