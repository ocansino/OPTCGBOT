const state = {
  current: null,
  replay: null,
  replaySide: "after",
  selectedCard: null,
  attack: { attacker: null },
  paletteIndex: 0,
  finder: { results: [], selected: null },
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
  cardTabBtn: document.querySelector("#cardTabBtn"),
  finderTabBtn: document.querySelector("#finderTabBtn"),
  chatPane: document.querySelector("#chatPane"),
  replayPane: document.querySelector("#replayPane"),
  cardPane: document.querySelector("#cardPane"),
  finderPane: document.querySelector("#finderPane"),
  consoleLog: document.querySelector("#consoleLog"),
  commandForm: document.querySelector("#commandForm"),
  commandInput: document.querySelector("#commandInput"),
  statusLine: document.querySelector("#statusLine"),
  cardDetails: document.querySelector("#cardDetails"),
  cardFinderForm: document.querySelector("#cardFinderForm"),
  cardFinderInput: document.querySelector("#cardFinderInput"),
  cardFinderCostInput: document.querySelector("#cardFinderCostInput"),
  cardFinderStatus: document.querySelector("#cardFinderStatus"),
  cardFinderResults: document.querySelector("#cardFinderResults"),
  cardFinderDetails: document.querySelector("#cardFinderDetails"),
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
  paletteBtn: document.querySelector("#paletteBtn"),
  paletteModal: document.querySelector("#paletteModal"),
  paletteCloseBtn: document.querySelector("#paletteCloseBtn"),
  paletteInput: document.querySelector("#paletteInput"),
  paletteList: document.querySelector("#paletteList"),
  helpBtn: document.querySelector("#helpBtn"),
  helpModal: document.querySelector("#helpModal"),
  helpCloseBtn: document.querySelector("#helpCloseBtn"),
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
  const showingChat = name === "chat";
  const showingReplay = name === "replay";
  const showingCard = name === "card";
  const showingFinder = name === "finder";
  els.chatTabBtn.classList.toggle("active", showingChat);
  els.replayTabBtn.classList.toggle("active", showingReplay);
  els.cardTabBtn.classList.toggle("active", showingCard);
  els.finderTabBtn.classList.toggle("active", showingFinder);
  els.chatPane.classList.toggle("active", showingChat);
  els.replayPane.classList.toggle("active", showingReplay);
  els.cardPane.classList.toggle("active", showingCard);
  els.finderPane.classList.toggle("active", showingFinder);
}

function showHelpModal() {
  els.helpModal.classList.remove("hidden");
  els.helpCloseBtn.focus();
}

function hideHelpModal() {
  els.helpModal.classList.add("hidden");
  els.helpBtn.focus();
}

function showPaletteModal() {
  state.paletteIndex = 0;
  els.paletteModal.classList.remove("hidden");
  els.paletteInput.value = "";
  renderPalette();
  els.paletteInput.focus();
}

function hidePaletteModal() {
  els.paletteModal.classList.add("hidden");
  els.commandInput.focus();
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

function playerIdFromZone(zoneLabel) {
  const match = String(zoneLabel || "").match(/\b(P1|P2)\b/);
  return match ? match[1] : null;
}

function zoneKind(zoneLabel) {
  if (String(zoneLabel || "").includes(" leader")) return "leader";
  if (String(zoneLabel || "").includes(" board")) return "board";
  return "";
}

function sameCard(left, right) {
  return Boolean(left?.instance_id && right?.instance_id && left.instance_id === right.instance_id);
}

function canUseAsAttacker(card, zoneLabel) {
  return Boolean(
    card?.instance_id &&
      !card.hidden &&
      playerIdFromZone(zoneLabel) === "P2" &&
      (zoneKind(zoneLabel) === "leader" || zoneKind(zoneLabel) === "board")
  );
}

function canUseAsAttackTarget(card, zoneLabel) {
  return Boolean(
    card?.instance_id &&
      !card.hidden &&
      playerIdFromZone(zoneLabel) === "P1" &&
      (zoneKind(zoneLabel) === "leader" || zoneKind(zoneLabel) === "board")
  );
}

function attackTargetToken(card, zoneLabel) {
  return zoneKind(zoneLabel) === "leader" ? "leader" : card.instance_id;
}

function selectedPlayerId() {
  return playerIdFromZone(state.selectedCard?.zoneLabel);
}

function selectedTargetToken() {
  const card = state.selectedCard;
  if (!card?.instance_id) return "";
  return zoneKind(card.zoneLabel) === "leader" ? "leader" : card.instance_id;
}

function insertCommand(command) {
  els.commandInput.value = command;
  els.commandInput.focus();
  if (command.endsWith(" ")) {
    els.commandInput.setSelectionRange(command.length, command.length);
  }
}

function setAttackAttacker(card, zoneLabel) {
  state.attack.attacker = { ...card, zoneLabel };
  state.selectedCard = { ...card, zoneLabel };
  setStatus(`${cardLabel(card)} selected to attack. Click an opponent leader or character to declare.`);
  render(state.current);
}

function clearAttackSelection(message = "") {
  state.attack.attacker = null;
  if (message) setStatus(message);
  if (state.current) render(state.current);
}

async function handleCardActivation(card, zoneLabel) {
  state.selectedCard = { ...card, zoneLabel };
  renderSelectedCard();
  showSideTab("card");
  if (state.busy || card?.hidden) return;

  const attacker = state.attack.attacker;
  if (!attacker) {
    if (canUseAsAttacker(card, zoneLabel)) {
      setAttackAttacker(card, zoneLabel);
    }
    return;
  }

  if (sameCard(attacker, card)) {
    clearAttackSelection("Attack selection canceled.");
    return;
  }

  if (canUseAsAttackTarget(card, zoneLabel)) {
    const command = `attack ${attacker.instance_id} ${attackTargetToken(card, zoneLabel)}`;
    state.attack.attacker = null;
    await submitCommand(command);
    return;
  }

  if (canUseAsAttacker(card, zoneLabel)) {
    setAttackAttacker(card, zoneLabel);
    return;
  }

  setStatus("Choose an opponent leader or character as the attack target.", "warning");
  render(state.current);
}

function renderCard(card, zoneLabel) {
  const tile = document.createElement("article");
  tile.className = "card-tile";
  const attacker = state.attack.attacker;
  if (sameCard(attacker, card)) {
    tile.classList.add("attack-attacker");
  } else if (attacker && canUseAsAttackTarget(card, zoneLabel)) {
    tile.classList.add("attack-target");
  } else if (attacker && card?.instance_id) {
    tile.classList.add("attack-muted");
  }
  if (canUseAsAttacker(card, zoneLabel)) {
    tile.title = "Click to select this attacker.";
  } else if (attacker && canUseAsAttackTarget(card, zoneLabel)) {
    tile.title = "Click to declare this attack target.";
  }
  tile.setAttribute("role", "button");
  tile.setAttribute("tabindex", "0");
  tile.addEventListener("click", () => {
    handleCardActivation(card, zoneLabel);
  });
  tile.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    handleCardActivation(card, zoneLabel);
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
  if (card.rush) stats.appendChild(makeChip("Rush", "good"));
  if (card.freeze) stats.appendChild(makeChip("Freeze", "warn"));
  if (card.cannot_attack) stats.appendChild(makeChip("No Attack", "warn"));
  if (card.cannot_rest) stats.appendChild(makeChip("No Rest", "warn"));

  tile.append(title, name, stats);
  if (card.instance_id && zoneLabel && zoneLabel.includes(" board")) {
    const playerId = playerIdFromZone(zoneLabel);
    if (playerId) tile.appendChild(makePowerControls(playerId, card.instance_id, "this card"));
  }
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

function makePowerControls(playerId, target, targetLabel = target) {
  const controls = document.createElement("div");
  controls.className = "power-controls";
  [
    ["-1000", -1000],
    ["+1000", 1000],
  ].forEach(([label, amount]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.title = `${label} power to ${playerId} ${targetLabel}`;
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

  const defenseSummary = document.createElement("div");
  defenseSummary.className = "defense-summary";
  if (prompt.type === "defense_choice") {
    const targetLabel = prompt.target_card?.card_id || prompt.target || "-";
    [
      ["Attacker", `${prompt.attacker?.card_id || "-"} / ${prompt.attacker_power ?? "-"}`],
      ["Target", `${targetLabel} / ${prompt.target_power ?? "-"}`],
      ["Counter needed", prompt.counter_needed ? `+${prompt.counter_needed}` : "none"],
      ["Tracked hand", `${prompt.hand_count ?? 0}`],
    ].forEach(([label, value]) => {
      const item = document.createElement("div");
      item.className = "defense-summary-item";
      const name = document.createElement("span");
      name.textContent = label;
      const stat = document.createElement("strong");
      stat.textContent = value;
      item.append(name, stat);
      defenseSummary.appendChild(item);
    });
  }

  const actions = document.createElement("div");
  actions.className = "prompt-actions";
  (prompt.choices || []).forEach((choice) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = choice.label;
    button.addEventListener("click", () => submitChoice(choice.command || choice.choice || choice.id));
    actions.appendChild(button);
  });

  els.promptPanel.append(title, message);
  if (prompt.type === "defense_choice") els.promptPanel.appendChild(defenseSummary);
  els.promptPanel.appendChild(actions);

  if (prompt.counter_card_input) {
    const counterRow = document.createElement("form");
    counterRow.className = "note-row";
    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.step = "1";
    input.max = String(Math.max(1, prompt.hand_count || 1));
    input.value = "1";
    input.setAttribute("aria-label", "Counter cards used");
    const button = document.createElement("button");
    button.type = "submit";
    button.textContent = "Counter";
    counterRow.append(input, button);
    counterRow.addEventListener("submit", (event) => {
      event.preventDefault();
      const count = Number.parseInt(input.value, 10);
      if (!Number.isFinite(count) || count <= 0) {
        setStatus("Counter card count must be greater than 0.", "warning");
        return;
      }
      submitChoice(`manual_counter:${count}`);
    });
    els.promptPanel.appendChild(counterRow);
  }

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

function makeActionButton(label, command, active = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.className = active ? "selected-action active" : "selected-action";
  button.addEventListener("click", () => submitCommand(command));
  return button;
}

function renderSelectedActions(card) {
  const playerId = selectedPlayerId();
  const target = selectedTargetToken();
  if (!playerId || !target || card.hidden) return null;

  const actions = document.createElement("div");
  actions.className = "selected-actions";

  const stateGroup = document.createElement("div");
  stateGroup.className = "selected-action-group";
  const stateTitle = document.createElement("div");
  stateTitle.className = "selected-action-title";
  stateTitle.textContent = "State";
  const stateButtons = document.createElement("div");
  stateButtons.className = "selected-action-row";
  stateButtons.append(
    makeActionButton("Active", `set ${card.instance_id} active`, card.state === "active"),
    makeActionButton("Rested", `set ${card.instance_id} rested`, card.state === "rested")
  );
  stateGroup.append(stateTitle, stateButtons);
  actions.appendChild(stateGroup);

  const powerGroup = document.createElement("div");
  powerGroup.className = "selected-action-group";
  const powerTitle = document.createElement("div");
  powerTitle.className = "selected-action-title";
  powerTitle.textContent = "Power";
  const powerButtons = document.createElement("div");
  powerButtons.className = "selected-action-row";
  powerButtons.append(
    makeActionButton("-1000", `power ${playerId} ${target} -1000`),
    makeActionButton("+1000", `power ${playerId} ${target} +1000`)
  );
  powerGroup.append(powerTitle, powerButtons);
  actions.appendChild(powerGroup);

  const flagGroup = document.createElement("div");
  flagGroup.className = "selected-action-group";
  const flagTitle = document.createElement("div");
  flagTitle.className = "selected-action-title";
  flagTitle.textContent = "Flags";
  const flagButtons = document.createElement("div");
  flagButtons.className = "selected-action-row";
  if (zoneKind(card.zoneLabel) === "board") {
    flagButtons.appendChild(makeActionButton("Rush", `${card.rush ? "unrush" : "rush"} ${card.instance_id}`, card.rush));
  }
  flagButtons.append(
    makeActionButton("Freeze", `${card.freeze ? "unfreeze" : "freeze"} ${card.instance_id}`, card.freeze),
    makeActionButton("No Attack", `${card.cannot_attack ? "can_attack" : "cannot_attack"} ${card.instance_id}`, card.cannot_attack),
    makeActionButton("No Rest", `${card.cannot_rest ? "can_rest" : "cannot_rest"} ${card.instance_id}`, card.cannot_rest)
  );
  flagGroup.append(flagTitle, flagButtons);
  actions.appendChild(flagGroup);

  return actions;
}

function renderSelectedCard() {
  refreshSelectedCardFromState();
  const card = state.selectedCard;
  els.cardDetails.innerHTML = "";
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
    `Rush: ${card.rush ? "yes" : "no"}`,
    `Freeze: ${card.freeze ? "yes" : "no"}`,
    `Cannot attack: ${card.cannot_attack ? "yes" : "no"}`,
    `Cannot rest: ${card.cannot_rest ? "yes" : "no"}`,
    `Attached DON: ${card.attached_don || 0}`,
    `Instance: ${card.instance_id || "-"}`,
  ];
  const details = document.createElement("pre");
  details.className = "selected-card-text";
  details.textContent = lines.join("\n");
  els.cardDetails.appendChild(details);
  const actions = renderSelectedActions(card);
  if (actions) els.cardDetails.appendChild(actions);
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

function finderCardSummary(card) {
  const parts = [];
  if (card.category) parts.push(card.category);
  if (card.cost !== null && card.cost !== undefined) parts.push(`Cost ${card.cost}`);
  if (card.power !== null && card.power !== undefined) parts.push(`Power ${card.power}`);
  if (card.counter) parts.push(`Counter +${card.counter}`);
  return parts.join(" | ");
}

function renderFinderResults() {
  els.cardFinderResults.innerHTML = "";
  els.cardFinderStatus.textContent = "";
  const results = state.finder.results || [];
  if (state.finder.total !== undefined) {
    const shown = results.length;
    const total = state.finder.total || 0;
    els.cardFinderStatus.textContent =
      shown === total ? `${total} result${total === 1 ? "" : "s"}` : `Showing ${shown} of ${total} results`;
  }
  if (!results.length) {
    const empty = document.createElement("div");
    empty.className = "empty-zone finder-empty";
    empty.textContent = els.cardFinderInput.value.trim().length >= 2 ? "No cards found." : "Search by card name, ID, and optional cost.";
    els.cardFinderResults.appendChild(empty);
    return;
  }
  results.forEach((card) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `finder-result ${state.finder.selected?.id === card.id ? "active" : ""}`;
    const title = document.createElement("div");
    title.className = "finder-result-title";
    title.textContent = `${card.id} - ${card.name || ""}`.trim();
    const meta = document.createElement("div");
    meta.className = "finder-result-meta";
    meta.textContent = finderCardSummary(card);
    button.append(title, meta);
    button.addEventListener("click", () => {
      state.finder.selected = card;
      renderFinder();
    });
    els.cardFinderResults.appendChild(button);
  });
}

function renderFinderDetails() {
  const card = state.finder.selected;
  els.cardFinderDetails.innerHTML = "";
  if (!card) {
    els.cardFinderDetails.textContent = "Select a search result.";
    return;
  }
  const title = document.createElement("div");
  title.className = "finder-detail-title";
  title.textContent = `${card.id} - ${card.name || ""}`.trim();
  const meta = document.createElement("div");
  meta.className = "finder-result-meta";
  meta.textContent = finderCardSummary(card);
  const typeLine = document.createElement("div");
  typeLine.className = "finder-result-meta";
  typeLine.textContent = [...(card.colors || []), ...(card.types || [])].join(" | ");
  const effect = document.createElement("pre");
  effect.className = "finder-effect";
  effect.textContent = card.effect || "No effect text.";
  const actions = document.createElement("div");
  actions.className = "selected-action-row finder-actions";
  actions.appendChild(makeActionButton("Play", `play ${card.id}`));
  actions.appendChild(makeActionButton("Play Rested", `play rested ${card.id}`));
  const insert = document.createElement("button");
  insert.type = "button";
  insert.className = "selected-action";
  insert.textContent = "Insert";
  insert.addEventListener("click", () => insertCommand(card.id));
  actions.appendChild(insert);
  els.cardFinderDetails.append(title, meta, typeLine, effect, actions);
}

function renderFinder() {
  renderFinderResults();
  renderFinderDetails();
}

async function searchCards(query) {
  const trimmed = query.trim();
  if (trimmed.length < 2) {
    state.finder = { results: [], selected: null };
    renderFinder();
    return;
  }
  setStatus("Searching cards...");
  try {
    const params = new URLSearchParams({ q: trimmed });
    const cost = els.cardFinderCostInput.value.trim();
    if (cost) params.set("cost", cost);
    const payload = await apiGet(`/api/cards?${params.toString()}`);
    state.finder.results = payload.cards?.results || [];
    state.finder.selected = state.finder.results[0] || null;
    state.finder.total = payload.cards?.total ?? state.finder.results.length;
    state.finder.limit = payload.cards?.limit ?? state.finder.results.length;
    renderFinder();
    setStatus(`Found ${state.finder.total} card${state.finder.total === 1 ? "" : "s"}.`);
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function paletteItems() {
  const card = state.selectedCard;
  const playerId = selectedPlayerId();
  const target = selectedTargetToken();
  const items = [
    { label: "Prepare Human Turn", detail: "Advance to your turn setup", action: () => postControl("/api/prepare-human-turn", "Preparing human turn") },
    { label: "End Human Turn", detail: "Pass turn back to the AI", action: () => postControl("/api/end-human-turn", "Ending human turn") },
    { label: "Run AI Turn", detail: "Let the AI act from the current state", action: () => postControl("/api/ai-turn", "Running AI turn") },
    { label: "Refresh View", detail: "Reload state from the local session", action: () => els.refreshBtn.click() },
    { label: "Save Match", detail: "Save the current game state", action: () => postControl("/api/save", "Saving") },
    { label: "New Game", detail: "Start a fresh physical-reported match", action: () => postControl("/api/new-game", "Starting new game", { match_mode: "physical_reported" }) },
    { label: "Play Card", detail: "Insert play command", insert: "play " },
    { label: "Play Card Rested", detail: "Insert played-rested command", insert: "play rested " },
    { label: "Attach DON To Leader", detail: "Insert attach command", insert: "attach 1 leader" },
    { label: "Find Card", detail: "Open card finder", action: () => showSideTab("finder") },
    { label: "Heal Life", detail: "Insert heal command", insert: "heal 1 P2" },
    { label: "Take Life", detail: "Insert take-life command", insert: "take_life 1 P2" },
    { label: "Draw Card", detail: "Insert draw command", insert: "draw 1 P2" },
    { label: "Trash Top Card", detail: "Insert trash-top command", insert: "trash_top 1 P2" },
    { label: "Reveal Top Cards", detail: "Insert reveal command", insert: "reveal_top 3 P2" },
  ];

  if (card?.instance_id && playerId && target && !card.hidden) {
    items.unshift(
      { label: `Set ${cardLabel(card)} Active`, detail: card.instance_id, command: `set ${card.instance_id} active` },
      { label: `Set ${cardLabel(card)} Rested`, detail: card.instance_id, command: `set ${card.instance_id} rested` },
      { label: `Add 1000 To ${cardLabel(card)}`, detail: card.instance_id, command: `power ${playerId} ${target} +1000` },
      { label: `Remove 1000 From ${cardLabel(card)}`, detail: card.instance_id, command: `power ${playerId} ${target} -1000` },
      { label: `${card.rush ? "Remove Rush From" : "Give Rush To"} ${cardLabel(card)}`, detail: card.instance_id, command: `${card.rush ? "unrush" : "rush"} ${card.instance_id}`, characterOnly: true },
      { label: `${card.freeze ? "Clear Freeze From" : "Freeze"} ${cardLabel(card)}`, detail: card.instance_id, command: `${card.freeze ? "unfreeze" : "freeze"} ${card.instance_id}` },
      { label: `${card.cannot_attack ? "Clear Cannot Attack From" : "Cannot Attack On"} ${cardLabel(card)}`, detail: card.instance_id, command: `${card.cannot_attack ? "can_attack" : "cannot_attack"} ${card.instance_id}` },
      { label: `${card.cannot_rest ? "Clear Cannot Rest From" : "Cannot Rest On"} ${cardLabel(card)}`, detail: card.instance_id, command: `${card.cannot_rest ? "can_rest" : "cannot_rest"} ${card.instance_id}` },
      { label: `Remove ${cardLabel(card)}`, detail: "Move selected card to trash", command: `remove ${card.instance_id}`, characterOnly: true }
    );
  }

  return items.filter((item) => !item.characterOnly || zoneKind(card?.zoneLabel) === "board");
}

function filteredPaletteItems() {
  const query = els.paletteInput.value.trim().toLowerCase();
  const items = paletteItems();
  if (!query) return items;
  return items.filter((item) => `${item.label} ${item.detail || ""} ${item.command || item.insert || ""}`.toLowerCase().includes(query));
}

function renderPalette() {
  const items = filteredPaletteItems();
  els.paletteList.innerHTML = "";
  if (state.paletteIndex >= items.length) state.paletteIndex = Math.max(0, items.length - 1);
  if (items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "palette-empty";
    empty.textContent = "No commands match.";
    els.paletteList.appendChild(empty);
    return;
  }
  items.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `palette-item ${index === state.paletteIndex ? "active" : ""}`;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", index === state.paletteIndex ? "true" : "false");
    const label = document.createElement("span");
    label.className = "palette-item-label";
    label.textContent = item.label;
    const detail = document.createElement("span");
    detail.className = "palette-item-detail";
    detail.textContent = item.detail || item.command || item.insert || "";
    button.append(label, detail);
    button.addEventListener("pointerenter", () => {
      state.paletteIndex = index;
    });
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      runPaletteItem(item);
    });
    els.paletteList.appendChild(button);
  });
}

function runPaletteItem(item) {
  hidePaletteModal();
  if (item.action) {
    item.action();
  } else if (item.insert) {
    insertCommand(item.insert);
  } else if (item.command) {
    submitCommand(item.command);
  }
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
  renderFinder();
  if (!els.paletteModal.classList.contains("hidden")) renderPalette();
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
    state.attack.attacker = null;
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
    state.attack.attacker = null;
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
    state.attack.attacker = null;
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
  postControl("/api/new-game", "Starting new game", { match_mode: "physical_reported" });
});
els.saveBtn.addEventListener("click", () => postControl("/api/save", "Saving"));
els.paletteBtn.addEventListener("click", showPaletteModal);
els.paletteCloseBtn.addEventListener("click", hidePaletteModal);
els.paletteModal.addEventListener("click", (event) => {
  if (event.target === els.paletteModal) hidePaletteModal();
});
els.paletteInput.addEventListener("input", () => {
  state.paletteIndex = 0;
  renderPalette();
});
els.paletteInput.addEventListener("keydown", (event) => {
  const items = filteredPaletteItems();
  if (event.key === "ArrowDown") {
    event.preventDefault();
    state.paletteIndex = Math.min(items.length - 1, state.paletteIndex + 1);
    renderPalette();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    state.paletteIndex = Math.max(0, state.paletteIndex - 1);
    renderPalette();
  } else if (event.key === "Enter") {
    event.preventDefault();
    if (items[state.paletteIndex]) runPaletteItem(items[state.paletteIndex]);
  }
});
els.helpBtn.addEventListener("click", showHelpModal);
els.helpCloseBtn.addEventListener("click", hideHelpModal);
els.helpModal.addEventListener("click", (event) => {
  if (event.target === els.helpModal) hideHelpModal();
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    showPaletteModal();
  } else if (event.key === "Escape" && !els.paletteModal.classList.contains("hidden")) {
    hidePaletteModal();
  } else if (event.key === "Escape" && !els.helpModal.classList.contains("hidden")) {
    hideHelpModal();
  } else if (event.key === "Escape" && state.attack.attacker) {
    clearAttackSelection("Attack selection canceled.");
  }
});
els.prepareBtn.addEventListener("click", () => postControl("/api/prepare-human-turn", "Preparing human turn"));
els.endHumanBtn.addEventListener("click", () => postControl("/api/end-human-turn", "Ending human turn"));
els.runAiBtn.addEventListener("click", () => postControl("/api/ai-turn", "Running AI turn"));
els.chatTabBtn.addEventListener("click", () => showSideTab("chat"));
els.replayTabBtn.addEventListener("click", () => showSideTab("replay"));
els.cardTabBtn.addEventListener("click", () => showSideTab("card"));
els.finderTabBtn.addEventListener("click", () => {
  showSideTab("finder");
  els.cardFinderInput.focus();
});
els.cardFinderForm.addEventListener("submit", (event) => {
  event.preventDefault();
  searchCards(els.cardFinderInput.value);
});
els.cardFinderCostInput.addEventListener("input", () => {
  const query = els.cardFinderInput.value.trim();
  if (query.length >= 2) searchCards(query);
});
els.cardFinderInput.addEventListener("input", () => {
  const query = els.cardFinderInput.value.trim();
  if (query.length < 2) {
    state.finder = { results: [], selected: null };
    renderFinder();
  }
});
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
