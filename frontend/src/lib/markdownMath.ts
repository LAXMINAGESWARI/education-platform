const FENCED_BLOCK = /(```[\s\S]*?```)/g;
const UNDECODED_FORMULA = /<!--\s*formula-not-decoded\s*-->/gi;
const WRAPPING_MARKDOWN_FENCE = /^```(?:markdown|md)[ \t]*\n([\s\S]*)\n```[ \t]*$/i;
const GRADE_VOICE_LINE = /^Write for .+ students studying .+\. Use plain language\.\s*$/gm;
const LESSON_JSON_START = /\{\s*"lesson_markdown"\s*:/g;
const LATEX_JSON_COLLISION =
  /^\\([bfnrt])(?=rac\b|imes\b|ext(?:bf|rm|it|tt|sf)?\b|egin\b|eg\b|ho\b|eta\b|heta\b|ar\b|inom\b|ight(?:arrow)?\b|orall\b|eq\b)/;

const LATEX_CONTROL_CHARS: ReadonlyArray<readonly [string, string]> = [
  ["\u000crac", "\\frac"],
  ["\u0008egin", "\\begin"],
  ["\u0008eta", "\\beta"],
  ["\u0008inom", "\\binom"],
  ["\u0009imes", "\\times"],
  ["\u0009ext", "\\text"],
  ["\u0009heta", "\\theta"],
  ["\u000dight", "\\right"],
];

function unescapeLatexBody(body: string): string {
  return body.replace(/\\\\/g, "\\");
}

function tightenDollarMath(markdown: string): string {
  let text = markdown.replace(/\$\$([\s\S]*?)\$\$/g, (_match, body: string) => {
    const inner = unescapeLatexBody(body.trim());
    return inner ? `$$${inner}$$` : "";
  });
  return text.replace(/\$([^$\n]+?)\$/g, (_match, body: string) => {
    const inner = unescapeLatexBody(body.trim());
    return inner ? `$${inner}$` : "";
  });
}

function convertEscapedDelimiters(markdown: string): string {
  let text = markdown.replace(
    /\\{1,2}\[([\s\S]*?)\\{1,2}\]/g,
    (_match, body: string) => `$$${unescapeLatexBody(body.trim())}$$`,
  );
  return text.replace(
    /\\{1,2}\(([\s\S]*?)\\{1,2}\)/g,
    (_match, body: string) => `$${unescapeLatexBody(body.trim())}$`,
  );
}

type JsonCursor = { text: string; i: number };

function repairLatexControls(text: string): string {
  let repaired = text.replace(/[\uE000-\uF8FF]/g, "");
  repaired = repaired.replace(/\s*\[Diagram\]\s*/gi, " ");
  for (const [broken, fixed] of LATEX_CONTROL_CHARS) {
    repaired = repaired.replaceAll(`\\${broken}`, fixed).replaceAll(broken, fixed);
  }
  return repaired;
}

function skipJsonSpace(cursor: JsonCursor): void {
  while (cursor.i < cursor.text.length && " \t\r\n".includes(cursor.text[cursor.i] ?? "")) {
    cursor.i += 1;
  }
}

function peekJson(cursor: JsonCursor): string {
  skipJsonSpace(cursor);
  return cursor.text[cursor.i] ?? "";
}

function parseJsonString(cursor: JsonCursor): string {
  if (cursor.text[cursor.i] !== '"') throw new Error("json string");
  cursor.i += 1;
  let out = "";
  while (cursor.i < cursor.text.length) {
    const ch = cursor.text[cursor.i] ?? "";
    if (ch === '"') {
      cursor.i += 1;
      return out;
    }
    if (ch !== "\\") {
      out += ch;
      cursor.i += 1;
      continue;
    }
    const collision = LATEX_JSON_COLLISION.exec(cursor.text.slice(cursor.i));
    if (collision) {
      out += `\\${cursor.text[cursor.i + 1] ?? ""}`;
      cursor.i += 2;
      continue;
    }
    const next = cursor.text[cursor.i + 1] ?? "";
    if (next === "n") out += "\n";
    else if (next === "r") out += "\r";
    else if (next === "t") out += "\t";
    else if (next === "b") out += "\b";
    else if (next === "f") out += "\f";
    else if (next === "u") {
      const digits = cursor.text.slice(cursor.i + 2, cursor.i + 6);
      if (/^[0-9a-fA-F]{4}$/.test(digits)) {
        out += String.fromCharCode(Number.parseInt(digits, 16));
        cursor.i += 6;
        continue;
      }
      out += "\\u";
    } else if (next === '"' || next === "\\" || next === "/") out += next;
    else out += `\\${next}`;
    cursor.i += 2;
  }
  throw new Error("json string");
}

function parseJsonValue(cursor: JsonCursor): unknown {
  const ch = peekJson(cursor);
  if (ch === "{") return parseJsonObject(cursor);
  if (ch === "[") return parseJsonArray(cursor);
  if (ch === '"') return parseJsonString(cursor);
  if (cursor.text.startsWith("true", cursor.i)) {
    cursor.i += 4;
    return true;
  }
  if (cursor.text.startsWith("false", cursor.i)) {
    cursor.i += 5;
    return false;
  }
  if (cursor.text.startsWith("null", cursor.i)) {
    cursor.i += 4;
    return null;
  }
  if (ch === "-" || (ch >= "0" && ch <= "9")) return parseJsonNumber(cursor);
  throw new Error("json value");
}

function parseJsonObject(cursor: JsonCursor): Record<string, unknown> {
  cursor.i += 1;
  const obj: Record<string, unknown> = {};
  if (peekJson(cursor) === "}") {
    cursor.i += 1;
    return obj;
  }
  while (cursor.i < cursor.text.length) {
    if (peekJson(cursor) !== '"') throw new Error("json key");
    const key = parseJsonString(cursor);
    if (peekJson(cursor) !== ":") throw new Error("json colon");
    cursor.i += 1;
    obj[key] = parseJsonValue(cursor);
    const next = peekJson(cursor);
    if (next === ",") {
      cursor.i += 1;
      if (peekJson(cursor) === "}") {
        cursor.i += 1;
        break;
      }
      continue;
    }
    if (next === "}") {
      cursor.i += 1;
      break;
    }
    throw new Error("json object");
  }
  return obj;
}

function parseJsonArray(cursor: JsonCursor): unknown[] {
  cursor.i += 1;
  const items: unknown[] = [];
  if (peekJson(cursor) === "]") {
    cursor.i += 1;
    return items;
  }
  while (cursor.i < cursor.text.length) {
    items.push(parseJsonValue(cursor));
    const next = peekJson(cursor);
    if (next === ",") {
      cursor.i += 1;
      if (peekJson(cursor) === "]") {
        cursor.i += 1;
        break;
      }
      continue;
    }
    if (next === "]") {
      cursor.i += 1;
      break;
    }
    throw new Error("json array");
  }
  return items;
}

function parseJsonNumber(cursor: JsonCursor): number {
  const start = cursor.i;
  if (cursor.text[cursor.i] === "-") cursor.i += 1;
  while (cursor.i < cursor.text.length && /\d/.test(cursor.text[cursor.i] ?? "")) cursor.i += 1;
  if (cursor.text[cursor.i] === ".") {
    cursor.i += 1;
    while (cursor.i < cursor.text.length && /\d/.test(cursor.text[cursor.i] ?? "")) cursor.i += 1;
  }
  const token = cursor.text.slice(start, cursor.i);
  if (!token || token === "-") throw new Error("json number");
  return Number(token);
}

function lessonMarkdownFromObject(text: string, start: number): { markdown: string; end: number } | null {
  try {
    const cursor: JsonCursor = { text, i: start };
    const value = parseJsonValue(cursor);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const record = value as Record<string, unknown>;
    const markdown = record.lesson_markdown ?? record.draft;
    if (typeof markdown !== "string" || !markdown.trim()) return null;
    return { markdown, end: cursor.i };
  } catch {
    return null;
  }
}

/** Replace `{"lesson_markdown": "..."}` envelopes with the markdown they wrap. */
export function unwrapEmbeddedLessonJson(markdown: string): string {
  let out = "";
  let cursor = 0;
  for (const match of markdown.matchAll(new RegExp(LESSON_JSON_START.source, "g"))) {
    const start = match.index ?? 0;
    if (start < cursor) continue;
    const parsed = lessonMarkdownFromObject(markdown, start);
    if (!parsed) continue;
    out += markdown.slice(cursor, start) + parsed.markdown;
    cursor = parsed.end;
  }
  return out + markdown.slice(cursor);
}

/** Drop the model instruction that stitch used to prepend to a lesson. */
export function stripGradeVoice(markdown: string): string {
  return markdown.replace(new RegExp(GRADE_VOICE_LINE.source, "gm"), "").replace(/\n{3,}/g, "\n\n");
}

/** Unwrap fences and JSON envelopes before math normalization or slide splitting. */
export function prepareLessonMarkdown(markdown: string): string {
  return stripGradeVoice(unwrapEmbeddedLessonJson(unwrapMarkdownFence(markdown)));
}

function repairDisplayMath(markdown: string): string {
  // `$$\begin{align*}` puts the environment in the fence meta, so KaTeX
  // only sees the rows and rejects `&`. A single trailing backslash is a
  // JSON-decoded `\\` row break.
  const opened = markdown.replace(
    /\$\$[ \t]*(\\begin\{)/g,
    (_match, begin: string) => `$$\n${begin}`,
  );
  return opened.replace(/\\begin\{align\*?\}[\s\S]*?\\end\{align\*?\}/g, (block) =>
    block.replace(/(?<!\\)\\(?=\n)/g, "\\\\"),
  );
}

function normalizeMathInProse(markdown: string): string {
  let text = repairLatexControls(markdown);
  text = text.replace(/\$\$(?:\$\$)+/g, () => "$$");
  text = text.replace(UNDECODED_FORMULA, "");
  text = convertEscapedDelimiters(text);
  text = text.replace(
    /(^|\n)[ \t]*\$\$([\s\S]*?)\$\$[ \t]*(?=\n|$)/g,
    (_match, lead: string, body: string) => `${lead}\n\n$$${unescapeLatexBody(body.trim())}$$\n\n`,
  );
  return repairDisplayMath(tightenDollarMath(text));
}

/** Strip a document-level ```markdown wrapper without touching inner fences. */
export function unwrapMarkdownFence(markdown: string): string {
  const trimmed = markdown.replace(/\r\n/g, "\n").trim();
  const match = WRAPPING_MARKDOWN_FENCE.exec(trimmed);
  return match ? match[1] : markdown.replace(/\r\n/g, "\n");
}

/** Make Docling / LLM math delimiters parseable by remark-math + KaTeX. */
export function normalizeMarkdownMath(markdown: string): string {
  return prepareLessonMarkdown(markdown)
    .split(FENCED_BLOCK)
    .map((part) => (part.startsWith("```") ? part : normalizeMathInProse(part)))
    .join("");
}
