/* Renders one recorded or live session, one turn at a time.
   Everything in the right column reflects the selected turn only - stepping
   back shows the state as it was, which is what makes the slot map readable. */

"use strict";

const el = (id) => document.getElementById(id);

const state = {
  manifest: null,
  session: null,
  sampleId: null,
  turn: 0,
  timer: null,
};

const PLAY_MS = 2200;

/* ── loading ───────────────────────────────────────────────────────────── */

async function getJSON(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function notify(message, tone) {
  const node = el("notice");
  node.hidden = !message;
  node.textContent = message || "";
  node.style.color = tone === "quiet" ? "var(--dim)" : "var(--signal)";
}

async function boot() {
  try {
    renderScoreboard(await getJSON("/api/metrics"));
  } catch (error) {
    notify(`Metrics unavailable: ${error.message}`);
  }
  try {
    state.manifest = await getJSON("/api/sessions");
  } catch (error) {
    notify(`${error.message}`);
    return;
  }
  renderPicker();
  // ?session=public_0004&turn=3 opens straight on a given turn, so a presenter
  // can bookmark the moment worth showing instead of clicking to it live.
  const params = new URLSearchParams(location.search);
  const known = state.manifest.sessions.some((s) => s.sample_id === params.get("session"));
  await selectSession(
    known ? params.get("session") : state.manifest.featured || state.manifest.sessions[0].sample_id,
    false,
    Number(params.get("turn")) - 1
  );
}

/* ── scoreboard ────────────────────────────────────────────────────────── */

function renderScoreboard(payload) {
  const current = payload.current;
  const baseline = payload.baseline;
  if (!current) {
    el("scoreboard").innerHTML =
      '<div class="metric"><dt>No results</dt><dd class="metric__value">—</dd></div>';
    return;
  }
  const rows = [
    ["Hit rate@10", current.hit_rate_at_10, baseline && baseline.hit_rate_at_10, 3],
    ["MRR", current.mrr, baseline && baseline.mrr, 3],
    ["Turns to find", current.mttc, baseline && baseline.mttc, 2],
    ["Technical score", current.recommended_technical_score, baseline && baseline.technical_score, 3],
  ];
  el("scoreboard").innerHTML = rows
    .map(([name, value, base, digits]) => {
      const shown = value == null ? "—" : Number(value).toFixed(digits);
      const delta = base == null ? "" :
        `<span class="metric__delta">baseline ${Number(base).toFixed(digits)}</span>`;
      return `<div class="metric"><dt>${name}</dt>` +
        `<dd class="metric__value">${shown}${delta}</dd></div>`;
    })
    .join("");

  if (!payload.live_enabled) {
    const button = el("runLive");
    button.disabled = true;
    button.querySelector(".run__label").textContent = "Live off";
  }
}

/* ── session picker ────────────────────────────────────────────────────── */

function renderPicker() {
  el("sessionList").innerHTML = state.manifest.sessions
    .map((session) => {
      const outcome = session.hit
        ? `hit turn ${session.first_hit_turn} · rank ${session.best_rank}`
        : "no hit";
      return `<li><button type="button" class="chip" data-id="${session.sample_id}"
        aria-pressed="false" title="${escapeHTML(session.note || "")}">
        <span class="chip__scenario">${session.scenario_type.replace("_", " ")}</span>
        ${outcome}</button></li>`;
    })
    .join("");

  el("sessionList").addEventListener("click", (event) => {
    const button = event.target.closest(".chip");
    if (button) selectSession(button.dataset.id);
  });
}

function markPicker() {
  document.querySelectorAll(".chip").forEach((chip) => {
    chip.setAttribute("aria-pressed", String(chip.dataset.id === state.sampleId));
  });
}

async function selectSession(sampleId, live, startTurn) {
  pause();
  state.sampleId = sampleId;
  markPicker();
  try {
    state.session = live
      ? await getJSON(`/api/run/${sampleId}`, { method: "POST" })
      : await getJSON(`/api/session/${sampleId}`);
  } catch (error) {
    notify(`Could not load ${sampleId}: ${error.message}`);
    return;
  }
  notify(
    state.session.note ||
      (state.session.live ? "Live run — the agent just produced this." : ""),
    state.session.live ? "loud" : "quiet"
  );
  el("stage").hidden = false;
  el("transport").hidden = false;
  renderThread();
  renderDots();
  goTo(Number.isFinite(startTurn) && startTurn >= 0 ? startTurn : 0);
}

/* ── the customer's side ───────────────────────────────────────────────── */

function renderThread() {
  el("thread").innerHTML = state.session.turns
    .map((turn) => {
      const isOverride = turn.turn === state.session.override_turn;
      const flags = [];
      if (isOverride) flags.push('<span class="flag flag--override">intent override</span>');
      if (turn.api_attribute)
        flags.push(`<span class="flag flag--ask">asked for ${turn.api_attribute}</span>`);
      if (turn.is_hit_turn)
        flags.push(`<span class="flag flag--hit">found · rank ${turn.target_rank}</span>`);

      // Flags live in a wrapper the turn hides until it has been reached, so the
      // ending is not sitting on screen before the session gets there.
      return `<li class="turn" data-turn="${turn.turn}">
        <span class="turn__marker">${turn.turn}</span>
        <p class="said">${escapeHTML(turn.user_message)}</p>
        <span class="flags">${flags.join(" ")}</span>
        <p class="said said--agent">${escapeHTML(turn.agent_message) || "<em>no message</em>"}</p>
      </li>`;
    })
    .join("");
}

/* ── the agent's side ──────────────────────────────────────────────────── */

function renderLedger(index) {
  el("ledger").innerHTML = state.session.turns
    .slice(0, index + 1)
    .map((turn, position) => {
      // Nothing is "added" on the opening turn - the whole query is the opening
      // message. Lighting all of it would spend the accent on no information.
      const seen =
        position === 0
          ? null
          : new Set(tokens(state.session.turns[position - 1].rewritten_query));
      const marked = tokens(turn.rewritten_query)
        .map((token) =>
          seen === null || seen.has(token)
            ? escapeHTML(token)
            : `<span class="tok--new">${escapeHTML(token)}</span>`
        )
        .join(" ");
      const current = position === index ? " ledger__row--current" : "";
      return `<li class="ledger__row${current}">
        <span class="ledger__turn">${turn.turn}</span>
        <span class="ledger__text">${marked || "<em>empty</em>"}</span>
      </li>`;
    })
    .join("");
}

function renderSlots(turn) {
  const names = Object.keys(turn.slots).sort();
  el("slotsEmpty").hidden = names.length > 0;
  el("slots").querySelector("tbody").innerHTML = names
    .map((name) => {
      const slot = turn.slots[name];
      const value = Array.isArray(slot.value) ? slot.value.join(", ") : slot.value;
      const flag = turn.slots_added.includes(name)
        ? "is-new"
        : turn.slots_changed.includes(name)
        ? "is-changed"
        : "";
      const strength = (state.session.final_slots[name] || {}).strength;
      const meta = [strength, `turn ${slot.source_turn}`].filter(Boolean).join(" · ");
      return `<tr class="${flag}">
        <td class="slot__name">${escapeHTML(name)}</td>
        <td class="slot__value">${escapeHTML(String(value))}</td>
        <td class="slot__meta">${escapeHTML(meta)}</td>
      </tr>`;
    })
    .join("");
}

function renderDecision(turn) {
  const asked = Boolean(turn.ask_attribute);
  const verdict = asked ? `asked about ${turn.ask_attribute}` : "stayed silent";
  const score =
    turn.question_score == null
      ? ""
      : ` · value ${Number(turn.question_score).toFixed(2)} vs bar ${Number(
          turn.question_threshold || 0
        ).toFixed(2)}`;
  el("decision").innerHTML =
    `<span class="decision__verdict">${escapeHTML(verdict)}</span>${escapeHTML(score)}` +
    `<br><span class="decision__reason">${escapeHTML(turn.decision_reason || "—")}</span>`;
}

function renderRuntime(turn) {
  const stages = Object.entries(turn.stage_milliseconds)
    .filter(([name]) => name !== "total")
    .map(([name, ms]) => `${name} ${Number(ms).toFixed(1)}`)
    .join(" · ");
  const total = turn.stage_milliseconds.total;
  const degraded = turn.degraded_stages.length > 0;
  const patches = turn.state_patches.length ? ` · patches ${turn.state_patches.join(" ")}` : "";
  el("runtime").innerHTML =
    `<span class="tier${degraded ? " tier--degraded" : ""}">tier ${escapeHTML(
      turn.fallback_tier || "unknown"
    )}</span> · ${total == null ? "—" : Number(total).toFixed(0)} ms` +
    escapeHTML(patches) +
    `<br><span class="runtime__stages">${escapeHTML(stages)}</span>`;
}

function renderRanked(turn) {
  if (!turn.recommendations.length) {
    el("ranked").innerHTML = '<li class="rank">nothing returned</li>';
    return;
  }
  el("ranked").innerHTML = turn.recommendations
    .map((item) => {
      const price = item.price == null ? "" : `$${Number(item.price).toFixed(2)}`;
      const badge = item.is_target && turn.is_hit_turn
        ? '<span class="rank__badge">found</span>'
        : "";
      return `<li class="rank${item.is_target ? " rank--target" : ""}">
        <span class="rank__n">${item.rank}</span>
        <span class="rank__title" title="${escapeHTML(item.title)}">${escapeHTML(item.title)}</span>
        <span class="rank__price">${price}</span>${badge}
      </li>`;
    })
    .join("");
}

/* ── transport ─────────────────────────────────────────────────────────── */

function renderDots() {
  el("dots").innerHTML = state.session.turns
    .map(
      (turn, index) =>
        `<li><button type="button" class="dot${
          turn.is_hit_turn ? " dot--hit" : ""
        }" data-index="${index}" aria-label="Turn ${turn.turn}"></button></li>`
    )
    .join("");
}

function goTo(index) {
  const turns = state.session.turns;
  state.turn = Math.max(0, Math.min(index, turns.length - 1));
  const turn = turns[state.turn];

  document.querySelectorAll(".turn").forEach((node, position) => {
    node.classList.toggle("turn--seen", position <= state.turn);
    node.classList.toggle("turn--current", position === state.turn);
  });
  document.querySelectorAll(".dot").forEach((dot, position) => {
    dot.setAttribute("aria-current", String(position === state.turn));
  });

  el("turnBadge").textContent = `turn ${turn.turn} of ${turns.length}`;
  renderLedger(state.turn);
  renderSlots(turn);
  renderDecision(turn);
  renderRuntime(turn);
  renderRanked(turn);

  const outcome = state.session.hit
    ? `found on turn ${state.session.first_hit_turn} at rank ${state.session.best_rank}`
    : "not found in 10 turns";
  el("transportRead").textContent =
    `${state.session.sample_id} · ${state.session.scenario_type.replace("_", " ")} · ${outcome}`;

  const current = document.querySelector(".turn--current");
  if (current) current.scrollIntoView({ block: "nearest", behavior: "smooth" });

  if (state.timer && state.turn === turns.length - 1) pause();
}

function play() {
  if (state.timer) return pause();
  if (state.turn === state.session.turns.length - 1) goTo(0);
  el("play").textContent = "❚❚";
  el("play").setAttribute("aria-label", "Pause the session");
  state.timer = setInterval(() => goTo(state.turn + 1), PLAY_MS);
}

function pause() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  const button = el("play");
  if (button) {
    button.textContent = "▶";
    button.setAttribute("aria-label", "Play the session");
  }
}

/* ── helpers ───────────────────────────────────────────────────────────── */

function tokens(text) {
  return String(text || "").split(/\s+/).filter(Boolean);
}

function escapeHTML(value) {
  return String(value == null ? "" : value).replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character])
  );
}

/* ── wiring ────────────────────────────────────────────────────────────── */

el("prev").addEventListener("click", () => { pause(); goTo(state.turn - 1); });
el("next").addEventListener("click", () => { pause(); goTo(state.turn + 1); });
el("play").addEventListener("click", play);
el("dots").addEventListener("click", (event) => {
  const dot = event.target.closest(".dot");
  if (dot) { pause(); goTo(Number(dot.dataset.index)); }
});

el("runLive").addEventListener("click", async () => {
  const button = el("runLive");
  button.disabled = true;
  button.querySelector(".run__label").textContent = "Running";
  notify("Running the agent…", "quiet");
  await selectSession(state.sampleId, true);
  button.disabled = false;
  button.querySelector(".run__label").textContent = "Run live";
});

document.addEventListener("keydown", (event) => {
  if (!state.session || event.target.closest("input, textarea")) return;
  if (event.key === "ArrowRight") { pause(); goTo(state.turn + 1); }
  else if (event.key === "ArrowLeft") { pause(); goTo(state.turn - 1); }
  else if (event.key === " ") { event.preventDefault(); play(); }
});

boot();
