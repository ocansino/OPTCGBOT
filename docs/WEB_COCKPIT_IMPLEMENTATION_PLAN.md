# Web Cockpit Implementation Plan

## Goal

Build a local, command-first web cockpit for GLAT that becomes the primary physical-table match surface while preserving the current Python engine, intake logic, replay logs, correction tools, and strict AI legality model.

Near-term success means:

> The player can open a local web page, report a physical turn with compact commands, resolve prompts with buttons, correct drift audibly, inspect the board/replay, and hand the resulting state to the AI without reconstruction work.

## Product Direction

The cockpit should feel like a match control desk, not a drag-and-drop simulator.

- Board state is the trust surface: leaders, characters, DON, life, hand summaries, deck/trash, battle context, selected card details.
- Chat/command log is the play surface: player commands, system confirmations, AI actions, corrections, prompts, unsupported-effect notes.
- Prompt chips/buttons handle choices: skip/manual done/note/implement later, searcher selections, blockers, counters, triggers, ambiguous card matches.
- Replay/debug tools are available but secondary: tabs or collapsible panels, not the main workflow.
- The frontend never implements rules. It renders display-safe state and submits commands/choices to Python.

## Current Reuse Points

Use the existing implementation as the first backend spine:

- `glat_engine.py`
  - Owns state, validation, action application, battle context, card effects, replay snapshots, manual helpers, AI turn execution.
- `operator_gui.py`
  - `process_console_command(engine, state, command)` is the best current adapter for web command submission.
  - `process_operator_command(...)` already handles pending prompts, physical reported play, corrections, shorthand, manual commands, and structured actions.
  - Display helpers already exist for summaries, replay lines, diffs, AI debug, battle trace, card details.
- `cli_intake.py`
  - Owns shorthand/session-memory behavior and should remain the structured reporting layer.
- `referee.py`
  - `get_legal_actions(state, engine)` remains part of AI strictness until moved or formalized.
- `tests/test_operator_gui.py`
  - Existing command-console tests should be mirrored for web endpoints.

## Proposed File Structure

First pass:

```text
web_app.py
web/
  static/
    index.html
    styles.css
    app.js
docs/
  WEB_COCKPIT_IMPLEMENTATION_PLAN.md
tests/
  test_web_cockpit.py
```

Optional second pass if the single file grows:

```text
web/
  server.py
  session.py
  serializers.py
  static/
```

Keep the first pass plain HTML/CSS/JS unless the cockpit outgrows it. Avoid introducing a frontend build chain before the backend contract is stable.

## Backend Plan

### 1. Add a Match Session Wrapper

Create a small wrapper around the engine and mutable state.

Responsibilities:

- Create/load a single in-memory match.
- Hold `GLATEngine` configured with the current AI agent choice.
- Expose command submission through `process_console_command`.
- Expose match controls: new game, save, load, prepare human turn, end human turn, run AI turn.
- Serialize state into a display-safe shape for the frontend.
- Persist to the current state path after mutating commands.

Initial shape:

```python
class WebMatchSession:
    def state_view(self) -> dict: ...
    def submit_command(self, command: str) -> dict: ...
    def submit_choice(self, choice: str) -> dict: ...
    def run_ai_turn(self) -> dict: ...
    def prepare_human_turn(self) -> dict: ...
    def end_human_turn(self) -> dict: ...
    def replay_view(self, position: int | None = None) -> dict: ...
```

### 2. Add Minimal HTTP API

Prefer FastAPI if dependency installation is acceptable; otherwise use Flask or the Python stdlib for a no-dependency prototype. FastAPI is the cleaner endpoint shape.

Endpoints:

- `GET /`
  - Serve the cockpit page.
- `GET /api/state`
  - Return display-safe state, command console messages, pending prompt, selected battle context, latest replay/diff/debug summaries.
- `POST /api/command`
  - Body: `{ "command": "play OP12-021" }`
  - Calls `process_console_command`.
  - Returns `{ ok, message, state, prompt }`.
- `POST /api/choice`
  - Body: `{ "choice": "skip effect" }`
  - First pass can route choices through `process_console_command`.
  - Later can carry structured choice ids.
- `POST /api/ai-turn`
  - Runs AI turn through existing engine flow.
- `POST /api/prepare-human-turn`
  - Reuses `ensure_human_turn_ready`.
- `POST /api/end-human-turn`
  - Applies end-turn behavior used by the match console.
- `GET /api/replay`
  - Returns replay entries and selected before/after/diff snapshots.
- `POST /api/new-game`
  - Starts a new match with `match_mode`, seed, and fake/real AI options.
- `POST /api/save`
  - Saves current state.
- `POST /api/load`
  - Loads selected state path.

### 3. Serialize Display-Safe State

Add a serializer that does not leak hidden AI cards.

Include:

- Match metadata: mode, turn, active player, phase.
- Per-player board:
  - leader card display
  - characters
  - DON area/spent/attached counts
  - life count plus known/revealed cards where allowed
  - hand count for hidden hands, full known hand only when appropriate
  - deck/trash counts and trash contents where public
- Command console entries from `state["command_console"]`.
- Pending prompt from `state["pending_console_prompt"]`.
- Latest battle trace, replay diff, AI debug summary.
- Selected card details when a frontend card id is selected.

Important boundary:

- In `physical_reported`, human-side reported known cards can be shown when the state has them.
- AI hidden hand/deck/life identities must stay hidden unless revealed by engine state.
- The frontend should never receive more hidden information than the current Tkinter/debug display intends to show.

### 4. Prompt Contract

First pass can expose existing pending prompt data directly enough to render buttons.

Normalize prompt output into:

```json
{
  "type": "unsupported_effect",
  "title": "Unsupported effect",
  "message": "OP12-021 text is not auto-resolved.",
  "choices": [
    { "id": "skip_effect", "label": "Skip", "command": "skip effect" },
    { "id": "manual_done", "label": "Manual Done", "command": "manual done" },
    { "id": "implement_later", "label": "Implement Later", "command": "implement later" }
  ],
  "allows_note": true
}
```

Later prompts should use the same shape for blockers, counters, triggers, search choices, and ambiguous card matches.

## Frontend Plan

### 1. Layout

Use a single responsive page:

- Left/center board region, taking at least half the width on desktop.
- Right command panel with log, prompt chips, command input, and match-control buttons.
- Bottom or tabbed debug region for replay/diff/AI debug/battle trace.
- On mobile or narrow windows, stack board first and command panel second, with sticky command input.

### 2. Board View

Render compact but readable zones:

- AI side at top, human side at bottom.
- Leader, board characters, DON, life, hand/deck/trash summaries.
- Hidden cards shown as card backs/count tiles.
- Public cards shown as compact tiles with card id, name, cost, power, state, attached DON.
- Click tile to show details in a side/detail pane.

Prioritize text clarity and stable layout over card art in the first pass.

### 3. Command Panel

Features:

- Chat-style timeline from `command_console`.
- Input box with submit on Enter.
- Buttons for common commands:
  - prepare human turn
  - end human turn
  - run AI turn
  - save
  - refresh
- Pending prompt chips rendered above the input.
- Note entry for unsupported effects when `allows_note` is true.
- Failed commands stay visible with warning styling.

### 4. Replay/Debug Panel

First pass:

- Latest diff.
- Battle trace.
- Recent AI debug.
- Replay list with selectable entries.

Second pass:

- Filters by turn, player, action type.
- Before/after snapshot toggle.
- Jump from log entry to replay entry.
- Jump from battle trace to related replay action.

## Implementation Phases

### Phase 1: Backend Skeleton

Deliverables:

- `web_app.py` starts a local server.
- Single in-memory match session.
- `GET /api/state` returns useful display-safe JSON.
- `POST /api/command` routes through `process_console_command`.
- Basic tests for state and command endpoints.

Acceptance tests:

- `play OP12-021` in `physical_reported` creates a human board card and returns an unsupported-effect prompt.
- `skip effect` clears the prompt.
- The response includes console messages and board state.
- `digital_strict` does not create untracked physical cards.

### Phase 2: First Usable Cockpit Page

Deliverables:

- Static `index.html`, `styles.css`, `app.js`.
- Board rendering for both players.
- Command log/input wired to `/api/command`.
- Prompt chips wired to `/api/choice` or `/api/command`.
- Match-control buttons wired to backend endpoints.

Acceptance tests:

- A user can perform the known physical-report flow from the browser:
  - prepare human turn
  - `play OP12-021`
  - resolve unsupported effect
  - end human turn
  - run AI turn
- Board and console update after each step without page refresh.

### Phase 3: Replay and Debug Trust

Deliverables:

- Replay list endpoint and panel.
- Latest diff, battle trace, and AI debug panels.
- Selected replay before/after display.

Acceptance tests:

- After a command, latest diff appears in the web UI.
- A selected replay entry displays before/after state and diff.
- AI turn debug data appears after AI action.

### Phase 4: Choice System Hardening

Deliverables:

- Structured prompt serializer for unsupported effects, ambiguous cards, blockers, counters, triggers, and searchers.
- Buttons/chips for all current GUI-native prompt classes.
- A single pending-choice lifecycle shared by GUI and web where practical.

Acceptance tests:

- Unsupported-effect flows still pass.
- Ambiguous `remove OP12-021` returns matches and a follow-up path.
- Trigger/counter/blocker prompts can be resolved from the browser without terminal/Tkinter fallback.

### Phase 5: Physical Match Pressure Pass

Deliverables:

- One long smoke script or regression test covering:
  - physical reported plays from outside hand
  - attacks
  - blocker/counter/trigger branches
  - unsupported effects
  - correction commands
  - AI responses
  - replay inspection
- A friction log in `docs/` for awkward commands and state-drift moments.

Acceptance tests:

- Multi-turn physical flow completes through only web endpoints.
- No command requires terminal or Tkinter interaction.
- Every correction creates an auditable entry and replay marker.

## Testing Strategy

Add `tests/test_web_cockpit.py` around the session/API layer.

Core tests:

- Initial state serialization hides AI hidden cards.
- Physical reported command flow matches current operator-console behavior.
- Pending unsupported-effect prompt serializes into choices.
- Prompt choice clears pending prompt.
- Correction command creates `operator_corrections` and replay entry.
- Replay endpoint returns entries and selected diffs.
- AI turn endpoint only uses engine-generated legal actions and records debug history.

Keep existing tests intact:

- `tests/test_glat_engine.py`
- `tests/test_operator_gui.py`

Web endpoint tests should reuse the same command scenarios as `test_operator_gui.py` so behavior does not fork.

## Risks and Guardrails

- Do not duplicate engine rules in JavaScript.
- Do not introduce free-form LLM parsing as part of the first cockpit.
- Do not leak AI hidden zones in `/api/state`.
- Do not block the web server on Tkinter dialogs; web mode needs non-GUI prompt providers.
- Do not make frontend polish the primary milestone before command flow is trustworthy.
- Keep `operator_gui.py` working as a debug/prototype surface while web stabilizes.
- Treat `process_console_command` as a bridge, not necessarily the final home for command orchestration. Once the web path proves out, extract shared command/session logic out of `operator_gui.py`.

## Recommended First PR

Scope the first implementation PR tightly:

1. Add `web_app.py` with a single in-memory `WebMatchSession`.
2. Add `GET /api/state` and `POST /api/command`.
3. Add display-safe state serialization.
4. Add a plain static cockpit page with board summary plus command log/input.
5. Add endpoint tests for the physical reported play and unsupported-effect prompt flow.

This gets the new direction breathing in a browser quickly, while keeping the current engine and console behavior as the authority.
