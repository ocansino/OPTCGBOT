import os
import json
import google.generativeai as genai
from dotenv import load_dotenv
load_dotenv()

class GeminiAgent:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")

    def get_action(self, state, legal_actions):
        prompt = self.build_prompt(state, legal_actions)

        for _ in range(3):
            response = self.model.generate_content(prompt)

            try:
                data = json.loads(response.text)
                idx = data["action_index"]

                if 0 <= idx < len(legal_actions):
                    return idx
            except:
                continue

        return 0  # fallback

    def build_prompt(self, state, legal_actions):
        return f"""
You are an AI playing the One Piece Card Game.

Game State:
{state}

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