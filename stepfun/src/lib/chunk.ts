import type { Chunk } from "./types";

function fnv1a(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function slugify(value: string): string {
  const slug = value
    .toLowerCase()
    .replace(/\.[a-z0-9]+$/, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "document";
}

function splitSentences(block: string): string[] {
  const sentences = block
    .split(/(?<=[.!?])\s+|\n+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  return sentences.length ? sentences : [block];
}

function pushHardSplit(parts: string[], text: string, maxChars: number): void {
  let rest = text;
  while (rest.length > maxChars) {
    let cut = rest.lastIndexOf(" ", maxChars);
    if (cut <= 0) cut = maxChars;
    parts.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) parts.push(rest);
}

export function chunkDocument(
  name: string,
  content: string,
  options: { maxChars?: number } = {},
): Chunk[] {
  const maxChars = Math.max(200, options.maxChars ?? 900);
  const normalized = content.replace(/\r\n?/g, "\n").trim();
  if (!normalized) return [];

  const documentId = `${slugify(name)}-${fnv1a(name)}`;
  const blocks = normalized
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean);

  const parts: string[] = [];
  for (const block of blocks) {
    if (block.length <= maxChars) {
      parts.push(block);
      continue;
    }
    let current = "";
    for (const sentence of splitSentences(block)) {
      if (!current) {
        current = sentence;
      } else if (current.length + sentence.length + 1 <= maxChars) {
        current = `${current} ${sentence}`;
      } else {
        parts.push(current);
        current = sentence;
      }
      if (current.length > maxChars) {
        pushHardSplit(parts, current, maxChars);
        current = "";
      }
    }
    if (current) parts.push(current);
  }

  return parts.map((text, index) => ({
    id: `${documentId}:${index}`,
    text,
    metadata: {
      documentId,
      title: name,
      source: "upload",
      chunkIndex: index,
    },
  }));
}
