import os
import json
from google import genai
from ai.parser import parse_action_response
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
            
        return 0  # fallback

    def build_prompt(self, state, legal_actions):
        state_json = json.dumps(state, indent=2)

        return f"""
You are an AI playing the One Piece Card Game.

Game State:
{state_json}

Legal Actions:
{self.format_actions(legal_actions)}

Return ONLY valid JSON:
{{
  "action_index": number,
  "reasoning": "short explanation"
}}
"""

    def format_actions(self, actions):
        return "\n".join([f"{i}: {a}" for i, a in enumerate(actions)])