const STOP_WORDS = new Set([
  "a",
  "an",
  "and",
  "are",
  "as",
  "at",
  "be",
  "by",
  "each",
  "equals",
  "find",
  "for",
  "from",
  "give",
  "gives",
  "how",
  "if",
  "in",
  "into",
  "is",
  "it",
  "its",
  "of",
  "on",
  "or",
  "over",
  "per",
  "than",
  "that",
  "the",
  "their",
  "then",
  "this",
  "to",
  "use",
  "what",
  "when",
  "where",
  "which",
  "whose",
  "with",
]);

const BINARY_COMMANDS = new Set([
  "approx",
  "cdot",
  "div",
  "equiv",
  "ge",
  "geq",
  "le",
  "leq",
  "mp",
  "ne",
  "neq",
  "pm",
  "times",
]);

type AtomKind = "space" | "op" | "num" | "ident" | "latex" | "word" | "other";

type Atom = { start: number; end: number; kind: AtomKind };

function isLetter(char: string): boolean {
  return /[A-Za-z]/.test(char);
}

function skipBrace(text: string, index: number): number {
  let depth = 0;
  let cursor = index;
  while (cursor < text.length) {
    const char = text[cursor] ?? "";
    if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth === 0) return cursor + 1;
    }
    cursor += 1;
  }
  return cursor;
}

function consumeScripts(text: string, index: number): number {
  let cursor = index;
  while (cursor < text.length) {
    const char = text[cursor] ?? "";
    if (char === "{") {
      cursor = skipBrace(text, cursor);
      continue;
    }
    if (char === "^" || char === "_") {
      cursor += 1;
      if (text[cursor] === "{") {
        cursor = skipBrace(text, cursor);
        continue;
      }
      if (/[A-Za-z0-9]/.test(text[cursor] ?? "")) {
        cursor += 1;
        continue;
      }
    }
    break;
  }
  return cursor;
}

function tokenize(text: string): Atom[] {
  const atoms: Atom[] = [];
  let index = 0;
  while (index < text.length) {
    const char = text[index] ?? "";
    if (/\s/.test(char)) {
      const start = index;
      while (index < text.length && /\s/.test(text[index] ?? "")) index += 1;
      atoms.push({ start, end: index, kind: "space" });
      continue;
    }
    if (char === "\\" && isLetter(text[index + 1] ?? "")) {
      const start = index;
      index += 1;
      while (index < text.length && isLetter(text[index] ?? "")) index += 1;
      index = consumeScripts(text, index);
      atoms.push({ start, end: index, kind: "latex" });
      continue;
    }
    if (char === "\\" && ",;!:".includes(text[index + 1] ?? "")) {
      atoms.push({ start: index, end: index + 2, kind: "latex" });
      index += 2;
      continue;
    }
    if (char === "^" || char === "_") {
      const start = index;
      index = consumeScripts(text, index);
      if (index > start) {
        atoms.push({ start, end: index, kind: "latex" });
        continue;
      }
    }
    if ("=+-*/()[],".includes(char)) {
      atoms.push({ start: index, end: index + 1, kind: "op" });
      index += 1;
      continue;
    }
    if (/\d/.test(char)) {
      const start = index;
      while (index < text.length && /[\d.]/.test(text[index] ?? "")) index += 1;
      atoms.push({ start, end: index, kind: "num" });
      continue;
    }
    if (isLetter(char)) {
      const start = index;
      while (index < text.length) {
        const next = text[index] ?? "";
        if (/[A-Za-z0-9]/.test(next)) {
          index += 1;
          continue;
        }
        if (next === "_" && text[index + 1] !== "{") {
          index += 1;
          continue;
        }
        break;
      }
      const word = text.slice(start, index);
      const ident = word.length === 1 || word.includes("_") || /\d/.test(word);
      atoms.push({ start, end: index, kind: ident ? "ident" : "word" });
      continue;
    }
    atoms.push({ start: index, end: index + 1, kind: "other" });
    index += 1;
  }
  return atoms;
}

function commandName(text: string, atom: Atom): string {
  let cursor = atom.start + 1;
  const start = cursor;
  while (cursor < atom.end && isLetter(text[cursor] ?? "")) cursor += 1;
  return text.slice(start, cursor);
}

function neighbor(atoms: Atom[], index: number, step: -1 | 1): Atom | null {
  let cursor = index + step;
  while (cursor >= 0 && cursor < atoms.length && atoms[cursor]?.kind === "space") {
    cursor += step;
  }
  return atoms[cursor] ?? null;
}

function isBinary(text: string, atom: Atom | null): boolean {
  if (!atom) return false;
  if (atom.kind === "op") return "=+-*/,(,".includes(text[atom.start] ?? "");
  if (atom.kind !== "latex") return false;
  return BINARY_COMMANDS.has(commandName(text, atom));
}

function canTakeWord(text: string, atom: Atom): boolean {
  if (atom.kind !== "word") return false;
  return !STOP_WORDS.has(text.slice(atom.start, atom.end).toLowerCase());
}

function expandSpan(
  atoms: Atom[],
  text: string,
  seed: number,
): { start: number; end: number; right: number } {
  let left = seed;
  let right = seed;
  let grew = true;
  while (grew) {
    grew = false;
    const next = neighbor(atoms, right, 1);
    const nextIndex = next ? atoms.indexOf(next) : -1;
    if (next && nextIndex > right) {
      const kind = next.kind;
      const take =
        kind === "latex" ||
        kind === "op" ||
        kind === "num" ||
        kind === "ident" ||
        (canTakeWord(text, next) && isBinary(text, atoms[right]?.kind === "space" ? neighbor(atoms, right, -1) : atoms[right]));
      if (take) {
        right = nextIndex;
        grew = true;
      }
    }
    const prev = neighbor(atoms, left, -1);
    const prevIndex = prev ? atoms.indexOf(prev) : -1;
    if (prev && prevIndex >= 0 && prevIndex < left) {
      const kind = prev.kind;
      const toward = neighbor(atoms, prevIndex, 1);
      const take =
        kind === "latex" ||
        kind === "op" ||
        kind === "num" ||
        kind === "ident" ||
        (canTakeWord(text, prev) && toward != null && text[toward.start] === "=");
      if (take) {
        left = prevIndex;
        grew = true;
      }
    }
  }
  return { start: atoms[left]?.start ?? 0, end: atoms[right]?.end ?? 0, right };
}

function trimCore(text: string, start: number, end: number): { start: number; end: number } {
  let coreStart = start;
  let coreEnd = end;
  while (coreStart < coreEnd && /\s/.test(text[coreStart] ?? "")) coreStart += 1;
  while (coreEnd > coreStart && /\s/.test(text[coreEnd - 1] ?? "")) coreEnd -= 1;
  while (coreEnd > coreStart && ",.;:".includes(text[coreEnd - 1] ?? "")) coreEnd -= 1;
  while (coreEnd > coreStart && /\s/.test(text[coreEnd - 1] ?? "")) coreEnd -= 1;
  return { start: coreStart, end: coreEnd };
}

function wrapBareSegment(segment: string): string {
  const atoms = tokenize(segment);
  let out = "";
  let cursor = 0;
  let index = 0;
  while (index < atoms.length) {
    if (atoms[index]?.kind !== "latex") {
      index += 1;
      continue;
    }
    const span = expandSpan(atoms, segment, index);
    const core = trimCore(segment, span.start, span.end);
    if (core.end <= core.start) {
      index += 1;
      continue;
    }
    out += segment.slice(cursor, core.start);
    out += `$${segment.slice(core.start, core.end)}$`;
    cursor = core.end;
    index = span.right + 1;
    while (index < atoms.length && (atoms[index]?.end ?? 0) <= cursor) index += 1;
  }
  return out + segment.slice(cursor);
}

function splitPreservingMath(text: string): { math: boolean; text: string }[] {
  const parts: { math: boolean; text: string }[] = [];
  let index = 0;
  let plainStart = 0;
  while (index < text.length) {
    if (text.startsWith("$$", index)) {
      const close = text.indexOf("$$", index + 2);
      if (close === -1) break;
      if (index > plainStart) parts.push({ math: false, text: text.slice(plainStart, index) });
      parts.push({ math: true, text: text.slice(index, close + 2) });
      index = close + 2;
      plainStart = index;
      continue;
    }
    if (text[index] === "$") {
      const close = text.indexOf("$", index + 1);
      if (close === -1 || text[close - 1] === "\\") {
        index += 1;
        continue;
      }
      const body = text.slice(index + 1, close);
      if (body.includes("\n")) {
        index += 1;
        continue;
      }
      if (index > plainStart) parts.push({ math: false, text: text.slice(plainStart, index) });
      parts.push({ math: true, text: text.slice(index, close + 1) });
      index = close + 1;
      plainStart = index;
      continue;
    }
    index += 1;
  }
  if (plainStart < text.length) parts.push({ math: false, text: text.slice(plainStart) });
  return parts;
}

/** Wrap bare LaTeX commands in `$...$` without touching math that is already delimited. */
export function delimitBareLatex(text: string): string {
  if (!text || !/\\[a-zA-Z]|[\^_]\{/.test(text)) return text;
  return splitPreservingMath(text)
    .map((part) => (part.math ? part.text : wrapBareSegment(part.text)))
    .join("");
}
