const fs = require("fs");
const path = require("path");

const projectRoot = path.resolve(__dirname, "..");
const deckListPath = path.join(projectRoot, "cards.txt");
const outputPath = path.join(projectRoot, "cards.json");
const cardsIndexPath = path.join(projectRoot, "cards", "index", "cards_by_id.json");
const cardsRootPath = path.join(projectRoot, "cards", "cards");

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function parseDeckLine(line, lineNumber) {
  const trimmed = line.trim();
  if (!trimmed) {
    return null;
  }

  const match = trimmed.match(/^(\d+)x([A-Za-z0-9-]+)$/);
  if (!match) {
    throw new Error(`Invalid deck entry on line ${lineNumber}: "${line}"`);
  }

  return {
    amount: Number.parseInt(match[1], 10),
    cardId: match[2].toUpperCase(),
    raw: trimmed,
  };
}

function resolveCardFile(cardId, indexEntry) {
  const packId = indexEntry.pack_id;
  const exactPath = path.join(cardsRootPath, packId, `${cardId}.json`);

  if (fs.existsSync(exactPath)) {
    return exactPath;
  }

  const packDir = path.join(cardsRootPath, packId);
  if (!fs.existsSync(packDir)) {
    throw new Error(`Pack directory not found for ${cardId}: ${packDir}`);
  }

  const fallbackFile = fs
    .readdirSync(packDir)
    .find((fileName) => fileName.toUpperCase() === `${cardId}.JSON`);

  if (fallbackFile) {
    return path.join(packDir, fallbackFile);
  }

  throw new Error(`Card file not found for ${cardId} in pack ${packId}`);
}

function main() {
  const deckLines = fs.readFileSync(deckListPath, "utf8").split(/\r?\n/);
  const deckEntries = deckLines
    .map((line, index) => parseDeckLine(line, index + 1))
    .filter(Boolean);

  const cardsById = readJson(cardsIndexPath);
  const missingCards = [];

  const cards = deckEntries.map((entry) => {
    const indexEntry = cardsById[entry.cardId];
    if (!indexEntry) {
      missingCards.push(entry.cardId);
      return null;
    }

    const cardFilePath = resolveCardFile(entry.cardId, indexEntry);
    const cardData = readJson(cardFilePath);

    return {
      amount: entry.amount,
      ...cardData,
    };
  });

  if (missingCards.length > 0) {
    throw new Error(`Missing card IDs in index: ${missingCards.join(", ")}`);
  }

  const output = {
    generated_at: new Date().toISOString(),
    source_deck: path.basename(deckListPath),
    cards,
  };

  fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  console.log(`Wrote ${cards.length} cards to ${outputPath}`);
}

main();
