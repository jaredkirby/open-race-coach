// Separate read-only client. No storage, simulator controls, or operational approval.
export function timingBasis(laps) {
  if (!Array.isArray(laps) || !laps.length) return "unknown";
  const sources = new Set(laps.map(lap => lap?.lap_time_source));
  if ([...sources].some(s => !["sim", "derived_from_ticks"].includes(s))) return "unknown";
  return sources.size > 1 ? "mixed" : sources.has("sim") ? "simulator_time" : "captured_time";
}
const timingLabels = { captured_time: "Captured timing", simulator_time: "Simulator-reported timing",
  mixed: "Mixed timing sources; combined pace and consistency withheld" };
const sourceLabels = { derived_from_ticks: "Captured", sim: "Simulator reported" };
const boundedInteger = (value, max = Number.MAX_SAFE_INTEGER) =>
  value === null || (Number.isSafeInteger(value) && value >= 0 && value <= max);
const CLOCK_TOLERANCE_MS = 500;
const CONTACT_TIMEOUT_MS = 3000;

export class LiveDisplay {
  constructor() {
    this.clock = null;
    this.clockEpoch = 0;
    this.disconnect("Not connected");
  }
  disconnect(reason = "Connection lost") {
    this.state = "disconnected"; this.reason = reason; this.telemetry = null;
    this.publisher = null; this.previous = null; this.deadline = 0; this.contact = null;
    this.feedback = null; this.operations = { state: "unknown" }; this.operationsDeadline = 0;
    this.operationsStamp = null;
  }
  observeClock(now, wall) {
    const previous = this.clock;
    const offset = wall - now;
    const valid = Number.isFinite(now) && Number.isFinite(wall) && Number.isFinite(offset);
    const changed = !valid || (previous && (now < previous.now || wall < previous.wall ||
      Math.abs(offset - previous.offset) > CLOCK_TOLERANCE_MS));
    if (changed) {
      this.clockEpoch += 1;
      this.disconnect("Clock changed; reconnecting");
    } else if (this.contact !== null && now - this.contact >= CONTACT_TIMEOUT_MS) {
      this.disconnect();
    }
    this.clock = valid ? { now, wall, offset: changed ? offset : previous?.offset ?? offset } : null;
  }
  beginRequest(nonce, sent, sentWall) {
    this.observeClock(sent, sentWall);
    return { nonce, sent, sentWall, clockEpoch: this.clockEpoch };
  }
  receive(packet, { nonce, sent, sentWall, clockEpoch, received, receivedWall }) {
    this.observeClock(received, receivedWall);
    if (clockEpoch !== this.clockEpoch) return;
    const monotonicRtt = received - sent, wallRtt = receivedWall - sentWall;
    if (!Number.isFinite(monotonicRtt) || !Number.isFinite(wallRtt) ||
        monotonicRtt < 0 || wallRtt < 0 ||
        Math.abs(monotonicRtt - wallRtt) > CLOCK_TOLERANCE_MS) {
      this.disconnect("Clock changed; reconnecting"); return;
    }
    const rtt = Math.max(monotonicRtt, wallRtt);
    if (!packet || packet.schema_version !== 1 || packet.request_id !== nonce ||
        !/^[a-f0-9]{32}$/.test(packet.publisher_id) ||
        !Number.isSafeInteger(packet.frame) || packet.frame < 0 ||
        (packet.source_epoch !== undefined &&
          (!Number.isSafeInteger(packet.source_epoch) || packet.source_epoch < 0)) ||
        !["waiting", "fresh", "stale"].includes(packet.state) ||
        !(packet.reason === null || (typeof packet.reason === "string" && packet.reason.length <= 80)) ||
        !Number.isFinite(packet.valid_for_ms) || packet.valid_for_ms < 0 ||
        packet.valid_for_ms > 2000) {
      this.disconnect("Invalid response"); return;
    }
    if (this.publisher !== packet.publisher_id) {
      this.disconnect("Waiting for new source progress");
      this.publisher = packet.publisher_id;
    }
    this.contact = received;
    this.feedback = null;
    const f = packet.feedback;
    if (f?.state === "reviewed" && /^[a-f0-9]{32}$/.test(f.review_id) &&
        ["live", "replay"].includes(f.evidence_mode) &&
        Number.isFinite(f.reviewed_at) && ["useful", "insufficient"].includes(f.verdict) &&
        ["captured_time", "simulator_time", "mixed"].includes(f.card?.timing_basis) &&
        f.card.timing_basis === timingBasis(f.card.laps) && f.card.scope === "one_recorded_session" &&
        f.card.context && ["car", "track", "session_type"].every(k =>
          typeof f.card.context[k] === "string" && f.card.context[k].length <= 256) &&
        Number.isFinite(f.card.ended_at) && Array.isArray(f.card.laps) && f.card.laps.length <= 128 &&
        f.card.laps.every(lap => Number.isSafeInteger(lap.lap) && Number.isFinite(lap.lap_time_s) &&
          typeof lap.valid === "boolean" && (lap.valid ? lap.invalid_reason === null :
            typeof lap.invalid_reason === "string" && lap.invalid_reason.length <= 80)) &&
        f.card.statistics && ["valid_laps", "total_lap_records", "best_s", "median_s", "spread_s"]
          .every(k => f.card.statistics[k] === null || Number.isFinite(f.card.statistics[k])) &&
        (f.card.timing_basis !== "mixed" || ["best_s", "median_s", "spread_s"].every(k =>
          f.card.statistics[k] === null) && f.card.objective === null) &&
        f.card.timing_groups && Object.entries(f.card.timing_groups).length <= 2 &&
        Object.entries(f.card.timing_groups).every(([source, stats]) => source in sourceLabels &&
          stats && ["valid_laps", "best_s", "median_s", "spread_s"].every(k =>
            stats[k] === null || Number.isFinite(stats[k]))) &&
        (f.card.objective === null || (Number.isSafeInteger(f.card.objective?.laps) &&
          Number.isFinite(f.card.objective.spread_s) &&
          f.card.objective.timing_basis === f.card.timing_basis))) this.feedback = f;
    const op = packet.operations;
    if (op && ["armed", "return_due", "unarmed", "suspended"].includes(op.state) &&
        /^[a-f0-9]{32}$/.test(op.service_id) && Number.isFinite(op.updated_at) &&
        Number.isFinite(op.valid_for_ms) && op.valid_for_ms > 0 && op.valid_for_ms <= 2000) {
      const stamp = JSON.stringify([op.service_id, op.updated_at, op.state]);
      const deadline = received + op.valid_for_ms - rtt;
      this.operations = { state: op.state };
      this.operationsDeadline = stamp === this.operationsStamp ? Math.min(this.operationsDeadline, deadline) : deadline;
      this.operationsStamp = stamp;
    } else { this.operations = { state: "unknown" }; this.operationsDeadline = 0; this.operationsStamp = null; }
    if (packet.state !== "fresh") {
      this.state = packet.state; this.reason = packet.reason || "Source unavailable";
      this.telemetry = null; this.previous = null; this.deadline = 0; return;
    }
    const data = packet.telemetry;
    const numeric = ["speed_mps", "rpm", "gear", "throttle_fraction", "brake_fraction",
      "steering_fraction", "fuel_litres", "lap", "lap_distance_m", "simulator_last_lap_s",
      "simulator_best_lap_s", "simulator_current_lap_s"];
    if (!/^[a-f0-9]{64}$/.test(packet.source_id) ||
        !Number.isSafeInteger(packet.source_sequence) || packet.source_sequence < 0 ||
        !Number.isSafeInteger(packet.source_samples) || packet.source_samples < 1 ||
        !Number.isFinite(packet.source_at) || !data || typeof data !== "object" ||
        numeric.some(key => !(key in data) || (data[key] !== null && !Number.isFinite(data[key]))) ||
        !data.context || ![data.context.car, data.context.track, data.context.variation]
          .every(text => text === null || (typeof text === "string" && text.length <= 256)) ||
        !boundedInteger(data.context.session_state, 6) || !boundedInteger(data.lap) ||
        data.view?.identity !== "viewed_car_unconfirmed" ||
        !boundedInteger(data.view.index, 63) || !boundedInteger(data.view.count, 64)) {
      this.disconnect("Invalid telemetry"); return;
    }
    const { car, track, variation, session_state } = data.context;
    const binding = JSON.stringify([packet.source_id, packet.source_epoch, car, track,
      variation, session_state, data.view.index, data.view.count]);
    const next = { binding, source: packet.source_id, sequence: packet.source_sequence, lap: data.lap,
      samples: packet.source_samples, at: packet.source_at, frame: packet.frame,
      payload: JSON.stringify(data) };
    const old = this.previous;
    const sameSample = old && next.source === old.source && next.sequence === old.sequence &&
      next.samples === old.samples && next.at === old.at;
    const duplicate = sameSample && next.binding === old.binding;
    const advances = old && next.binding === old.binding && next.sequence > old.sequence &&
      next.samples > old.samples && next.at > old.at && next.frame > old.frame &&
      !(next.lap !== null && old.lap !== null && next.lap < old.lap);
    if (sameSample && (next.payload !== old.payload || next.frame < old.frame)) {
      this.disconnect("Repeated sample changed"); return;
    }
    const deadline = received + packet.valid_for_ms - rtt;
    this.deadline = duplicate ? Math.min(this.deadline, deadline) : deadline;
    if (this.deadline <= received || (duplicate && this.state === "stale")) {
      if (!duplicate) this.previous = null;
      this.state = "stale"; this.reason = "Source reading expired";
      this.telemetry = null; return;
    }
    this.previous = next;
    if (!old || (!duplicate && !advances)) {
      this.state = "waiting"; this.reason = "Waiting for source progress";
      this.telemetry = null; return;
    }
    if (duplicate && this.state === "waiting") return;
    this.state = "fresh"; this.reason = "Receiving telemetry"; this.telemetry = data;
  }
  view(now, wall) {
    this.observeClock(now, wall);
    if (this.state === "fresh" && now >= this.deadline) {
      this.state = "stale"; this.reason = "Source reading expired"; this.telemetry = null;
    }
    const operations = this.state === "fresh" && now < this.operationsDeadline
      ? this.operations : { state: "unknown" };
    return { state: this.state, reason: this.reason, telemetry: this.telemetry,
      feedback: this.feedback, operations };
  }
}

if (typeof document !== "undefined") {
  const display = new LiveDisplay();
  const byId = id => document.getElementById(id);
  let code = "", generation = 0, pending = false;
  let renderedFeedback = null;
  const show = (id, value, digits = 0) => {
    byId(id).textContent = value === null || value === undefined ? "—" : Number(value).toFixed(digits);
  };
  function paint() {
    const value = display.view(performance.now(), Date.now());
    byId("connection").textContent = value.state.toUpperCase();
    byId("connection").dataset.state = value.state;
    byId("reason").textContent = value.reason.replaceAll("_", " ");
    const d = value.telemetry;
    const modes = ["", "Practice", "Test", "Qualifying", "Formation", "Race", "Time Trial"];
    byId("context").textContent = d ? [d.context.car, d.context.track, d.context.variation,
      modes[d.context.session_state]]
      .filter(Boolean).join(" · ") : "Waiting for current car and track";
    show("speed", d?.speed_mps == null ? null : d.speed_mps * 3.6);
    show("rpm", d?.rpm); show("gear", d?.gear); show("fuel", d?.fuel_litres, 2);
    show("throttle", d?.throttle_fraction == null ? null : d.throttle_fraction * 100);
    show("brake", d?.brake_fraction == null ? null : d.brake_fraction * 100);
    show("lap", d?.lap); show("last", d?.simulator_last_lap_s, 3);
    show("best", d?.simulator_best_lap_s, 3);
    byId("validity").textContent = !d || d.lap_invalidated === null ? "—" :
      d.lap_invalidated ? "Invalidated" : "No invalidation reported";
    const supervisionLabels = { armed: "Fuel supervision active", return_due: "Fuel: pit this lap",
      unarmed: "Fuel supervision unarmed", suspended: "Fuel supervision suspended",
      unknown: "Fuel supervision unverified" };
    byId("supervision").textContent = supervisionLabels[value.operations.state];
    const feedback = value.feedback, card = feedback?.card;
    const feedbackKey = JSON.stringify(feedback);
    if (feedbackKey !== renderedFeedback) {
      renderedFeedback = feedbackKey;
      byId("feedback-body").hidden = !card;
      byId("feedback-status").textContent = card
        ? `Chief reviewed · ${feedback.evidence_mode === "replay" ? "Replay evidence" : "Recorded evidence"} · ${new Date(feedback.reviewed_at * 1000).toLocaleString()}`
        : "No reviewed segment selected";
      byId("feedback-laps").replaceChildren();
      if (card) {
        byId("feedback-context").textContent = `${card.context.car} · ${card.context.track} · ${card.context.session_type}`;
        byId("feedback-scope").textContent = `One recorded segment ending ${new Date(card.ended_at * 1000).toLocaleString()}. ${timingLabels[card.timing_basis]}.`;
        const s = card.statistics, seconds = v => v == null ? "—" : v.toFixed(3) + " s";
        byId("feedback-summary").textContent = `${s.valid_laps} valid / ${s.total_lap_records} lap records · Best ${seconds(s.best_s)} · Median ${seconds(s.median_s)} · Full spread ${seconds(s.spread_s)}`;
        byId("feedback-timing-groups").textContent = card.timing_basis === "mixed"
          ? Object.entries(card.timing_groups).map(([source, group]) =>
            `${sourceLabels[source]}: ${group.valid_laps} valid · Best ${seconds(group.best_s)} · Median ${seconds(group.median_s)} · Full spread ${seconds(group.spread_s)}`).join("; ") : "";
        for (const lap of card.laps) {
          const row = document.createElement("tr");
          for (const text of [lap.lap, `${seconds(lap.lap_time_s)} · ${sourceLabels[lap.lap_time_source]}`, lap.valid ? "Valid" : lap.invalid_reason.replaceAll("_", " ")]) {
            const cell = document.createElement("td"); cell.textContent = text; row.append(cell);
          }
          byId("feedback-laps").append(row);
        }
      }
    }
    if (card) {
      const norm = value => value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
      const modes = ["", "practice", "practice", "qualifying", "race", "race", "hotlap"];
      const matches = d && norm(card.context.car) === norm(d.context.car || "") &&
        norm(card.context.track) === norm([d.context.track, d.context.variation].filter(Boolean).join(" ")) &&
        card.context.session_type === modes[d.context.session_state];
      byId("feedback-objective").textContent = card.objective && matches
        ? `Next consistency target: ${card.objective.laps} valid laps within ${card.objective.spread_s.toFixed(2)} s. Keep every lap in the review.`
        : card.objective ? "Historical target: match car, track and mode before using it."
          : "Insufficient evidence for a consistency target.";
    }
  }
  async function poll() {
    if (!code || pending || document.hidden) return;
    pending = true;
    const current = generation;
    const request = display.beginRequest(crypto.randomUUID(), performance.now(), Date.now());
    try {
      const response = await fetch("/v1/telemetry", { cache: "no-store",
        headers: { Authorization: `Bearer ${code}`, "X-ORC-Request-ID": request.nonce },
        signal: AbortSignal.timeout(1500) });
      if (!response.ok) throw new Error(response.status === 401 ? "Connection code rejected" : "Connection lost");
      const text = await response.text();
      if (text.length > 131072) throw new Error("Response too large");
      if (current === generation) display.receive(JSON.parse(text),
        { ...request, received: performance.now(), receivedWall: Date.now() });
    } catch (error) {
      if (current === generation) display.disconnect(error.message === "Connection code rejected"
        ? error.message : "Connection lost");
    } finally { pending = false; paint(); }
  }
  byId("connect-form").addEventListener("submit", event => {
    event.preventDefault(); code = byId("code").value.trim(); byId("code").value = "";
    generation += 1; display.disconnect("Connecting"); paint(); poll();
  });
  byId("disconnect").addEventListener("click", () => {
    code = ""; generation += 1; display.disconnect(); paint();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { generation += 1; display.disconnect("Page hidden"); paint(); }
    else { paint(); poll(); }
  });
  setInterval(poll, 500); setInterval(paint, 100); paint();
}
