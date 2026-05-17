from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


SUPPORTED_AUTOMATIC_EFFECT_IDS = {
    "EB03-042",
    "EB03-053",
    "OP06-115",
    "OP10-109",
    "OP12-086",
    "OP12-087",
    "OP12-089",
    "OP07-085",
    "OP12-094",
    "EB04-058",
    "OP12-097",
    "OP12-098",
    "OP12-112",
    "EB03-056",
    "OP12-119",
    "OP14-108",
}

ENGINE_EFFECT_NOTES = {
    "OP12-081": (
        "implemented_or_partial",
        "leader attack draw and opponent-play reaction are implemented",
    ),
    "OP09-062": ("implemented_static", "Banish is implemented for successful life damage"),
    "OP12-086": ("implemented_or_partial", "on-play top-3 search is implemented"),
    "EB03-042": ("implemented_or_partial", "static cost and opponent-turn on-KO play are implemented"),
    "OP12-089": ("implemented_or_partial", "blocker/static cost and on-KO removal are implemented"),
    "OP07-085": ("implemented_or_partial", "on-play trash-own-character cost and K.O. target are implemented"),
    "OP12-093": ("implemented_static", "Revolutionary Army static cost increase is implemented"),
    "OP12-087": ("implemented_or_partial", "blocker/static cost and on-play discard are implemented"),
    "PRB02-014": ("implemented_static", "blocker and trash-count cost discount are implemented"),
    "OP12-094": ("implemented_or_partial", "on-play recycle and play-from-trash are implemented"),
    "EB04-058": ("implemented_or_partial", "blocker and low-life top-deck-to-life on-play are implemented"),
    "OP10-109": ("implemented_or_partial", "on-KO life trash and trigger draw/discard are implemented"),
    "OP12-112": ("implemented_or_partial", "trigger draw is implemented"),
    "EB03-056": ("implemented_or_partial", "on-play face-up life cost and cost-3-or-less K.O. are implemented"),
    "EB03-053": ("implemented_or_partial", "on-play effect and on-KO face-up life/play-from-hand are implemented"),
    "OP12-119": ("implemented_or_partial", "on-play and opponent-turn on-KO life gain are implemented"),
    "OP14-108": ("implemented_or_partial", "on-play KO and trigger activation of on-play effect are implemented"),
    "OP12-097": ("implemented_or_partial", "main search and trigger activation of main effect are implemented"),
    "OP12-098": ("implemented_or_partial", "counter bonus preview and trigger draw/trash are implemented"),
    "OP06-115": ("implemented_or_partial", "counter cost/bonus and 0-life trigger are implemented"),
}


TIMING_PATTERNS = (
    ("activate_main", re.compile(r"\[Activate:\s*Main\]", re.IGNORECASE)),
    ("on_play", re.compile(r"\[On Play\]", re.IGNORECASE)),
    ("on_attack", re.compile(r"When this .*attacks|When this .*attacks", re.IGNORECASE)),
    ("on_ko", re.compile(r"\[On K\.O\.\]", re.IGNORECASE)),
    ("opponents_turn", re.compile(r"\[Opponent'?s Turn\]", re.IGNORECASE)),
    ("once_per_turn", re.compile(r"\[Once Per Turn\]", re.IGNORECASE)),
    ("blocker", re.compile(r"\[Blocker\]", re.IGNORECASE)),
    ("trigger", re.compile(r"\[Trigger\]|^.+$", re.IGNORECASE)),
    ("static", re.compile(r"\bgains\b|\bhas\b|\bgets\b|\bif your Leader\b", re.IGNORECASE)),
)


def load_deck(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return list(payload.get("cards", []))


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("<br>", " ")).strip()


def classify_timings(effect_text: str, trigger_text: str) -> List[str]:
    timings: List[str] = []
    combined = " ".join(part for part in (effect_text, trigger_text) if part)
    for name, pattern in TIMING_PATTERNS:
        if name == "trigger":
            if trigger_text:
                timings.append(name)
            continue
        if pattern.search(combined):
            timings.append(name)
    if combined and not timings:
        timings.append("other")
    return timings


def implementation_status(card_id: str, effect_text: str, trigger_text: str) -> str:
    if not effect_text and not trigger_text:
        return "no_effect_text"
    if card_id in ENGINE_EFFECT_NOTES:
        return ENGINE_EFFECT_NOTES[card_id][0]
    if card_id in SUPPORTED_AUTOMATIC_EFFECT_IDS:
        return "automatic_or_partial"
    return "manual_required"


def implementation_note(card_id: str) -> str:
    return ENGINE_EFFECT_NOTES.get(card_id, ("", ""))[1]


def inventory_cards(cards: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for card in cards:
        card_id = str(card.get("id", "")).upper()
        effect_text = clean_text(card.get("effect"))
        trigger_text = clean_text(card.get("trigger"))
        rows.append(
            {
                "amount": int(card.get("amount", 0) or 0),
                "card_id": card_id,
                "name": card.get("name", card_id),
                "category": card.get("category"),
                "cost": card.get("cost"),
                "power": card.get("power"),
                "types": list(card.get("types", [])),
                "timings": classify_timings(effect_text, trigger_text),
                "status": implementation_status(card_id, effect_text, trigger_text),
                "implementation_note": implementation_note(card_id),
                "effect": effect_text,
                "trigger": trigger_text,
            }
        )
    return rows


def status_counts(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        status = row["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def format_text_report(rows: List[Dict[str, Any]]) -> str:
    lines = ["Effect inventory for AI deck", ""]
    counts = status_counts(rows)
    lines.append(
        "Summary: "
        + ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    )
    lines.append("")
    for row in rows:
        timings = ", ".join(row["timings"]) if row["timings"] else "-"
        lines.append(
            f"{row['card_id']} x{row['amount']} | {row['name']} | "
            f"{row['category']} | status={row['status']} | timings={timings}"
        )
        if row["effect"]:
            lines.append(f"  effect: {row['effect']}")
        if row["trigger"]:
            lines.append(f"  trigger: {row['trigger']}")
        if row["implementation_note"]:
            lines.append(f"  implementation: {row['implementation_note']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Inventory effect text for the current AI deck.")
    parser.add_argument("--deck", default="cards.json", help="Path to generated deck JSON.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    rows = inventory_cards(load_deck(Path(args.deck)))
    if args.json:
        print(json.dumps({"summary": status_counts(rows), "cards": rows}, indent=2))
    else:
        print(format_text_report(rows), end="")


if __name__ == "__main__":
    main()
