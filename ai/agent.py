import os
import json
from google import genai
from ai.parser import parse_action_response, parse_turn_plan_response
from dotenv import load_dotenv
load_dotenv()


class GeminiAgent:
    def __init__(self):
        self.client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY")
        )
        self.model = "gemini-2.5-flash"

    def get_action(self, state, legal_actions):
        prompt = self.build_prompt(state, legal_actions)

        for _ in range(3):
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            raw_text = response.text or ""

            try:
                idx = parse_action_response(raw_text, len(legal_actions))
                return idx
            except Exception as e:
                print("RAW RESPONSE:", raw_text)
                print("PARSE ERROR:", e)
                continue

        return len(legal_actions) - 1

    def get_turn_plan(self, state, legal_actions):
        prompt = self.build_turn_plan_prompt(state, legal_actions)

        for _ in range(3):
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            raw_text = response.text or ""

            try:
                return parse_turn_plan_response(raw_text, len(legal_actions))
            except Exception as e:
                print("RAW RESPONSE:", raw_text)
                print("PARSE ERROR:", e)
                continue

        return [len(legal_actions) - 1]

    def build_prompt(self, state, legal_actions):
        state_json = json.dumps(state, indent=2)

        return f"""
You are selecting the next legal action for a One Piece Card Game engine.

CRITICAL RULES:
- Only choose from the listed legal actions.
- Do NOT invent actions.
- Return ONLY raw JSON.
- The engine will reject illegal moves.
- If unsure, choose End turn.

Game State:
{state_json}

Legal Actions:
{self.format_actions(legal_actions)}

Return ONLY valid JSON:
{{
  "action_index": number
}}
"""

    def build_turn_plan_prompt(self, state, legal_actions):
        state_json = json.dumps(state, indent=2)

        return f"""
You are selecting a legal turn plan for a One Piece Card Game engine.

CRITICAL RULES:
- Only choose from the listed legal actions.
- Do NOT invent actions.
- Return ONLY raw JSON.
- Use action indices exactly as listed.
- Keep the plan short and realistic.
- Include End turn as the final action when appropriate.
- If unsure, return only End turn.

Game State:
{state_json}

Legal Actions:
{self.format_actions(legal_actions)}

Return ONLY valid JSON:
{{
  "planned_actions": [number, number]
}}
"""

    def format_actions(self, actions):
        formatted = []
        for i, action in enumerate(actions):
            if action["type"] == "play_card":
                formatted.append(f"{i}: Play card {action['payload']['card_id']}")
            elif action["type"] == "attach_don":
                formatted.append(
                    f"{i}: Attach {action['payload']['amount']} DON to {action['payload']['card_id']}"
                )
            elif action["type"] == "attack":
                formatted.append(
                    f"{i}: Attack with {action['payload']['attacker_id']} -> {action['payload']['target']}"
                )
            else:
                formatted.append(f"{i}: End turn")
        return "\n".join(formatted)
