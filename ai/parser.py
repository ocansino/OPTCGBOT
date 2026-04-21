import json
import re


def extract_json(text: str) -> str:
    """Remove markdown/code fences from LLM output."""
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*", "", text)
        text = re.sub(r"```$", "", text)

    return text.strip()


def parse_action_response(text: str, num_actions: int) -> int:
    """
    Parse LLM response and return a valid action index.
    Raises ValueError if parsing fails.
    """
    cleaned = extract_json(text)

    data = json.loads(cleaned)

    if "action_index" not in data:
        raise ValueError("Missing action_index")

    idx = data["action_index"]

    if not isinstance(idx, int):
        raise ValueError("action_index must be int")

    if not (0 <= idx < num_actions):
        raise ValueError("action_index out of range")

    return idx