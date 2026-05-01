const state = {
  current: null,
  replay: null,
  replaySide: "after",
  selectedCard: null,
  busy: false,
};

const els = {
  matchMeta: document.querySelector("#matchMeta"),
  aiBoard: document.querySelector("#aiBoard"),
  humanBoard: document.querySelector("#humanBoard"),
  battleTrace: document.querySelector("#battleTrace"),
  promptPanel: document.querySelector("#promptPanel"),
  chatTabBtn: document.querySelector("#chatTabBtn"),
  replayTabBtn: document.querySelector("#replayTabBtn"),
  chatPane: document.querySelector("#chatPane"),
  replayPane: document.querySelector("#replayPane"),
  consoleLog: document.querySelector("#consoleLog"),
  commandForm: document.querySelector("#commandForm"),
  commandInput: document.querySelector("#commandInput"),
  statusLine: document.querySelector("#statusLine"),
  cardDetails: document.querySelector("#cardDetails"),
  latestDiff: document.querySelector("#latestDiff"),
  aiDebug: document.querySelector("#aiDebug"),
  replayList: document.querySelector("#replayList"),
  replayDiff: document.querySelector("#replayDiff"),
  replaySnapshot: document.querySelector("#replaySnapshot"),
  replayBeforeBtn: document.querySelector("#replayBeforeBtn"),
  replayAfterBtn: document.querySelector("#replayAfterBtn"),
  refreshBtn: document.querySelector("#refreshBtn"),
  newGameBtn: document.querySelector("#newGameBtn"),
  saveBtn: document.querySelector("#saveBtn"),
  prepareBtn: document.querySelector("#prepareBtn"),
  endHumanBtn: document.querySelector("#endHumanBtn"),
  runAiBtn: document.querySelector("#runAiBtn"),
};

function setBusy(nextBusy) {
  state.busy = nextBusy;
  document.querySelectorAll("button, input").forEach((element) => {
    element.disabled = nextBusy;
  });
}

function setStatus(message, kind = "info") {
  els.statusLine.textContent = message;
  els.statusLine.dataset.kind = kind;
}

function showSideTab(name) {
  const showingReplay = name === "replay";
  els.chatTabBtn.classList.toggle("active", !showingReplay);
  els.replayTabBtn.classList.toggle("active", showingReplay);
  els.chatPane.classList.toggle("active", !showingReplay);
  els.replayPane.classList.toggle("active", showingReplay);
}

async function apiGet(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.message || `Request failed: ${response.status}`);
  }
  return payload;
}

async function apiPost(path, body = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.message || `Request failed: ${response.status}`);
  }
  return payload;
}

function lineList(lines) {
  return (lines || []).join("\n");
}

function cardLabel(card) {
  if (!card) return "-";
  if (card.hidden) return "Hidden";
  return card.card_id || card.name || "-";
}

function cardName(card) {
  if (!card) return "";
  if (card.hidden) return "Hidden card";
  return card.name || card.card_id || "";
}

function makeChip(text, extraClass = "") {
  const span = document.createElement("span");
  span.className = `stat-chip ${extraClass}`.trim();
  span.textContent = text;
  return span;
}

function displayPower(card) {
  return card.current_power ?? card.power;
}

function powerBonusText(card) {
  const bonus = (card.manual_power_bonus || 0) + (card.battle_power_bonus || 0);
  if (!bonus) return "";
  return bonus > 0 ? `+${bonus}` : String(bonus);
}

function canToggleCardState(card) {
  return card && card.instance_id && (card.state === "active" || card.state === "rested");
}

function makeStateChip(card) {
  const nextState = card.state === "rested" ? "active" : "rested";
  const chip = makeChip(card.state, card.state === "rested" ? "warn state-toggle" : "state-toggle");
  chip.title = `Set ${cardLabel(card)} ${nextState}`;
  chip.setAttribute("role", "button");
  chip.setAttribute("tabindex", "0");
  chip.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleCardState(card);
  });
  chip.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.stopPropagation();
    toggleCardState(card);
  });
  return chip;
}

function renderCard(card, zoneLabel) {
  const tile = document.createElement("article");
  tile.className = "card-tile";
  tile.setAttribute("role", "button");
  tile.setAttribute("tabindex", "0");
  tile.addEventListener("click", () => {
    state.selectedCard = { ...card, zoneLabel };
    renderSelectedCard();
  });
  tile.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    state.selectedCard = { ...card, zoneLabel };
    renderSelectedCard();
  });

  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = cardLabel(card);

  const name = document.createElement("div");
  name.className = "card-name";
  name.textContent = cardName(card);

  const stats = document.createElement("div");
  stats.className = "card-stats";
  if (card.category) stats.appendChild(makeChip(card.category));
  if (card.cost !== null && card.cost !== undefined) stats.appendChild(makeChip(`Cost ${card.cost}`));
  if (card.power !== null && card.power !== undefined) {
    const bonus = powerBonusText(card);
    stats.appendChild(makeChip(`Power ${displayPower(card)}${bonus ? ` (${bonus})` : ""}`, bonus ? "good" : ""));
  }
  if (card.counter) stats.appendChild(makeChip(`+${card.counter}`, "good"));
  if (card.state) {
    stats.appendChild(canToggleCardState(card) ? makeStateChip(card) : makeChip(card.state, card.state === "rested" ? "warn" : ""));
  }
  if (card.attached_don) stats.appendChild(makeChip(`DON ${card.attached_don}`, "good"));
  if (card.has_blocker) stats.appendChild(makeChip("Blocker", "warn"));

  tile.append(title, name, stats);
  return tile;
}

function renderCardRow(cards, zoneLabel) {
  const row = document.createElement("div");
  row.className = "card-row";
  if (!cards || cards.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-zone";
    empty.textContent = "empty";
    row.appendChild(empty);
    return row;
  }
  cards.forEach((card) => row.appendChild(renderCard(card, zoneLabel)));
  return row;
}

function makePowerControls(playerId, target) {
  const controls = document.createElement("div");
  controls.className = "power-controls";
  [
    ["-1000", -1000],
    ["+1000", 1000],
  ].forEach(([label, amount]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.title = `${label} power to ${playerId} ${target}`;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const sign = amount > 0 ? "+" : "";
      submitCommand(`power ${playerId} ${target} ${sign}${amount}`);
    });
    controls.appendChild(button);
  });
  return controls;
}

function renderZone(title, summary, children, actions = null) {
  const zone = document.createElement("div");
  zone.className = "zone";

  const heading = document.createElement("div");
  heading.className = "zone-title";
  const name = document.createElement("span");
  name.textContent = title;
  const count = document.createElement("span");
  count.className = "zone-summary";
  count.textContent = summary || "";
  const meta = document.createElement("div");
  meta.className = "zone-meta";
  meta.appendChild(count);
  if (actions) meta.appendChild(actions);
  heading.append(name, meta);

  zone.append(heading, children);
  return zone;
}

function renderPile(label, value) {
  const tile = document.createElement("div");
  tile.className = "pile-tile";
  const labelEl = document.createElement("div");
  labelEl.className = "pile-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("div");
  valueEl.className = "pile-value";
  valueEl.textContent = value;
  tile.append(labelEl, valueEl);
  return tile;
}

function renderPlayer(player, match) {
  const wrapper = document.createElement("div");

  const header = document.createElement("div");
  header.className = "player-header";
  const title = document.createElement("div");
  const playerName = document.createElement("div");
  playerName.className = "player-name";
  playerName.textContent = `${player.label} (${player.id})`;
  const playerSummary = document.createElement("div");
  playerSummary.className = "card-subtitle";
  playerSummary.textContent = `Life ${player.life} | Hand ${player.hand_count} | Deck ${player.deck_count} | Trash ${player.trash_count}`;
  title.append(playerName, playerSummary);
  header.appendChild(title);
  if (match.active_player === player.id) {
    const active = document.createElement("span");
    active.className = "active-pill";
    active.textContent = "Active";
    header.appendChild(active);
  }
  wrapper.appendChild(header);

  const zones = document.createElement("div");
  zones.className = "zones";

  zones.appendChild(renderZone("Leader", "", renderCardRow([player.leader], `${player.id} leader`), makePowerControls(player.id, "leader")));
  zones.appendChild(renderZone("Board", `${player.board.length}/5`, renderCardRow(player.board, `${player.id} board`), makePowerControls(player.id, "board")));

  const piles = document.createElement("div");
  piles.className = "pile-grid";
  piles.appendChild(renderPile("Life", player.life_count));
  piles.appendChild(renderPile("Hand", player.hand_count));
  piles.appendChild(renderPile("Deck", player.deck_count));
  piles.appendChild(renderPile("Trash", player.trash_count));
  piles.appendChild(renderPile("DON Area", player.don.area_count));
  piles.appendChild(renderPile("DON Spent", player.don.spent_count));
  zones.appendChild(renderZone("Resources", `Attached ${player.don.attached_total}`, piles));

  wrapper.appendChild(zones);
  return wrapper;
}

function renderConsole(consoleState) {
  els.consoleLog.innerHTML = "";
  const entries = consoleState?.entries || [];
  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-zone";
    empty.textContent = "No console messages yet.";
    els.consoleLog.appendChild(empty);
    return;
  }

  entries.forEach((entry) => {
    const item = document.createElement("article");
    item.className = `log-entry ${entry.kind || "system"}`;

    const meta = document.createElement("div");
    meta.className = "log-meta";
    const index = document.createElement("span");
    index.textContent = `#${entry.index || "-"} | T${entry.turn || "-"}`;
    const speaker = document.createElement("span");
    speaker.textContent = `${entry.speaker || "System"} | ${entry.phase || "-"}`;
    meta.append(index, speaker);

    const message = document.createElement("div");
    message.textContent = entry.message || "";

    item.append(meta, message);
    els.consoleLog.appendChild(item);
  });
  els.consoleLog.scrollTop = els.consoleLog.scrollHeight;
}

function renderPrompt(prompt) {
  els.promptPanel.innerHTML = "";
  els.promptPanel.classList.toggle("hidden", !prompt);
  if (!prompt) return;

  const title = document.createElement("h2");
  title.className = "prompt-title";
  title.textContent = prompt.title || "Pending prompt";

  const message = document.createElement("p");
  message.className = "prompt-message";
  message.textContent = prompt.message || "";

  const actions = document.createElement("div");
  actions.className = "prompt-actions";
  (prompt.choices || []).forEach((choice) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = choice.label;
    button.addEventListener("click", () => submitChoice(choice.command || choice.choice || choice.id));
    actions.appendChild(button);
  });

  els.promptPanel.append(title, message, actions);

  if (prompt.allows_note) {
    const noteRow = document.createElement("form");
    noteRow.className = "note-row";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "note blocker text ignored";
    input.setAttribute("aria-label", "Prompt note");
    const button = document.createElement("button");
    button.type = "submit";
    button.textContent = "Note";
    noteRow.append(input, button);
    noteRow.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (text) submitChoice(`note ${text}`);
    });
    els.promptPanel.appendChild(noteRow);
  }
}

function renderSelectedCard() {
  refreshSelectedCardFromState();
  const card = state.selectedCard;
  if (!card) {
    els.cardDetails.textContent = "Select a visible card.";
    return;
  }

  const lines = [
    `${cardLabel(card)} ${card.name ? `- ${card.name}` : ""}`.trim(),
    `Zone: ${card.zoneLabel || "-"}`,
    `Category: ${card.category || "-"}`,
    `Cost: ${card.cost ?? "-"}`,
    `Power: ${displayPower(card) ?? "-"}`,
    `Power bonus: ${powerBonusText(card) || "0"}`,
    `Counter: ${card.counter ?? "-"}`,
    `State: ${card.state || "-"}`,
    `Attached DON: ${card.attached_don || 0}`,
    `Instance: ${card.instance_id || "-"}`,
  ];
  els.cardDetails.textContent = lines.join("\n");
}

function visibleCardsWithZones(stateView) {
  const players = stateView?.players || {};
  return Object.values(players).flatMap((player) => {
    if (!player) return [];
    const cards = [];
    if (player.leader) cards.push({ ...player.leader, zoneLabel: `${player.id} leader` });
    (player.board || []).forEach((card) => cards.push({ ...card, zoneLabel: `${player.id} board` }));
    return cards;
  });
}

function refreshSelectedCardFromState() {
  if (!state.selectedCard?.instance_id || !state.current) return;
  const currentCard = visibleCardsWithZones(state.current).find(
    (card) => card.instance_id === state.selectedCard.instance_id
  );
  if (currentCard) {
    state.selectedCard = currentCard;
  }
}

function snapshotPlayerCard(player) {
  const card = document.createElement("article");
  card.className = "snapshot-card";
  const title = document.createElement("h3");
  title.textContent = `${player.label || player.id} (${player.id})`;
  card.appendChild(title);

  const rows = [
    ["Life", player.life ?? player.life_count ?? "-"],
    ["Hand", player.hand_count ?? (player.hand || []).length],
    ["Board", (player.board || []).length],
    ["Deck", player.deck_count ?? (player.deck || []).length],
    ["Trash", player.trash_count ?? (player.trash || []).length],
  ];
  rows.forEach(([label, value]) => {
    const row = document.createElement("div");
    row.className = "snapshot-line";
    const left = document.createElement("span");
    left.textContent = label;
    const right = document.createElement("strong");
    right.textContent = String(value);
    row.append(left, right);
    card.appendChild(row);
  });
  return card;
}

function renderReplaySnapshot(snapshot) {
  els.replaySnapshot.innerHTML = "";
  if (!snapshot || !snapshot.players) {
    const empty = document.createElement("div");
    empty.className = "empty-zone";
    empty.textContent = "No snapshot selected.";
    els.replaySnapshot.appendChild(empty);
    return;
  }
  els.replaySnapshot.appendChild(snapshotPlayerCard(snapshot.players.P1));
  els.replaySnapshot.appendChild(snapshotPlayerCard(snapshot.players.P2));
}

function renderReplay() {
  const replay = state.replay;
  els.replayList.innerHTML = "";
  if (!replay || !replay.entries || replay.entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-zone";
    empty.textContent = "No replay entries yet.";
    els.replayList.appendChild(empty);
    els.replayDiff.textContent = "No replay diff yet.";
    renderReplaySnapshot(null);
    return;
  }

  replay.entries.forEach((entry) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `replay-entry ${entry.position === replay.selected_position ? "active" : ""}`;
    const title = document.createElement("div");
    title.className = "replay-entry-title";
    title.textContent = entry.label || `Step ${entry.index}`;
    const meta = document.createElement("div");
    meta.className = "replay-entry-meta";
    meta.textContent = `Turn ${entry.turn ?? "-"} | ${entry.player || "-"} | ${entry.action_type || "-"}`;
    button.append(title, meta);
    button.addEventListener("click", () => loadReplay(entry.position));
    els.replayList.appendChild(button);
  });

  const selected = replay.selected;
  els.replayDiff.textContent = selected ? lineList(selected.diff_lines) : "No replay diff yet.";
  const snapshot = selected ? selected[state.replaySide] : null;
  renderReplaySnapshot(snapshot);
}

function render(stateView) {
  state.current = stateView;
  const match = stateView.match;
  els.matchMeta.textContent = `Mode ${match.mode} | Turn ${match.turn} | Active ${match.active_player} | Phase ${match.phase} | Winner ${match.winner || "-"}`;

  els.aiBoard.innerHTML = "";
  els.aiBoard.appendChild(renderPlayer(stateView.players.P1, match));
  els.humanBoard.innerHTML = "";
  els.humanBoard.appendChild(renderPlayer(stateView.players.P2, match));

  els.battleTrace.textContent = lineList(stateView.debug?.battle_trace);
  els.latestDiff.textContent = lineList(stateView.debug?.latest_diff);
  els.aiDebug.textContent = lineList(stateView.debug?.ai_debug);

  renderConsole(stateView.console);
  renderPrompt(stateView.prompt);
  renderSelectedCard();
  renderReplay();
}

async function refreshState() {
  const payload = await apiGet("/api/state");
  render(payload.state);
  await refreshReplay();
}

async function refreshReplay(position = null) {
  const query = position === null ? "" : `?position=${encodeURIComponent(position)}`;
  const payload = await apiGet(`/api/replay${query}`);
  state.replay = payload.replay;
  renderReplay();
}

async function loadReplay(position) {
  setBusy(true);
  setStatus("Loading replay step...");
  try {
    await refreshReplay(position);
    setStatus("Replay step loaded.");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function submitCommand(command) {
  const trimmed = command.trim();
  if (!trimmed) return;
  setBusy(true);
  setStatus("Sending...");
  try {
    const payload = await apiPost("/api/command", { command: trimmed });
    render(payload.state);
    await refreshReplay();
    setStatus(payload.message || (payload.ok ? "Command applied." : "Command was not applied."), payload.ok ? "success" : "warning");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
    els.commandInput.focus();
  }
}

async function toggleCardState(card) {
  if (state.busy || !canToggleCardState(card)) return;
  const nextState = card.state === "rested" ? "active" : "rested";
  await submitCommand(`set ${card.instance_id} ${nextState}`);
}

async function submitChoice(choice) {
  setBusy(true);
  setStatus("Resolving prompt...");
  try {
    const payload = await apiPost("/api/choice", { choice });
    render(payload.state);
    await refreshReplay();
    setStatus(payload.message || "Prompt resolved.", payload.ok ? "success" : "warning");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
    els.commandInput.focus();
  }
}

async function postControl(path, label, body = {}) {
  setBusy(true);
  setStatus(`${label}...`);
  try {
    const payload = await apiPost(path, body);
    render(payload.state);
    await refreshReplay();
    setStatus(payload.message || label, payload.ok ? "success" : "warning");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
    els.commandInput.focus();
  }
}

els.commandForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const command = els.commandInput.value;
  els.commandInput.value = "";
  submitCommand(command);
});

els.refreshBtn.addEventListener("click", async () => {
  setBusy(true);
  setStatus("Refreshing...");
  try {
    await refreshState();
    setStatus("Refreshed.");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
  }
});

els.newGameBtn.addEventListener("click", () => {
  postControl("/api/new-game", "Starting new game", { match_mode: "physical_reported", seed: 7 });
});
els.saveBtn.addEventListener("click", () => postControl("/api/save", "Saving"));
els.prepareBtn.addEventListener("click", () => postControl("/api/prepare-human-turn", "Preparing human turn"));
els.endHumanBtn.addEventListener("click", () => postControl("/api/end-human-turn", "Ending human turn"));
els.runAiBtn.addEventListener("click", () => postControl("/api/ai-turn", "Running AI turn"));
els.chatTabBtn.addEventListener("click", () => showSideTab("chat"));
els.replayTabBtn.addEventListener("click", () => showSideTab("replay"));
els.replayBeforeBtn.addEventListener("click", () => {
  state.replaySide = "before";
  renderReplay();
});
els.replayAfterBtn.addEventListener("click", () => {
  state.replaySide = "after";
  renderReplay();
});

refreshState()
  .then(() => setStatus("Ready"))
  .catch((error) => setStatus(error.message, "error"));
