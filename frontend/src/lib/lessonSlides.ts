import type { LessonMaterial, LessonSlide } from "../api/types";
import { prepareLessonMarkdown } from "./markdownMath";

const CANONICAL_SLIDE = /^## Slide (\d+)\s*[—\-]\s*(.+?)\s*$/;
const H1_HEADING = /^#\s+(.+?)\s*$/;
const H2_LINE = /^##\s+(.+?)\s*$/;
const H3_LINE = /^###\s+(.+?)\s*$/;
const HAS_SECTION = /^#{2,3}\s+/m;
/** A phone screen of prose, plus a short table or formula that belongs with it. */
const SLIDE_TARGET_CHARS = 3400;

export function findSummarySlide(slides: LessonSlide[]): LessonSlide | null {
  return slides.find((slide) => /lesson summary/i.test(slide.title)) ?? slides.at(-1) ?? null;
}

/** Prefer API-parsed slides; otherwise split markdown into slide-sized sections. */
export function slidesForLessonView(lesson: LessonMaterial): LessonSlide[] {
  if (lesson.slides.length > 0) {
    return lesson.slides;
  }
  return slidesFromMarkdown(lesson.markdown, lesson.title);
}

export function slidesFromMarkdown(markdown: string, fallbackTitle: string): LessonSlide[] {
  const normalized = prepareLessonMarkdown(markdown).trim();
  if (!normalized) {
    return [];
  }
  const canonical = splitByHeading(normalized, CANONICAL_SLIDE, (match) => ({
    number: Number(match[1]),
    title: match[2]?.trim() || fallbackTitle || "Lesson",
  }));
  if (canonical.length > 0) {
    return canonical;
  }
  const h1 = splitByHeading(normalized, H1_HEADING, (match, index) => ({
    number: index + 1,
    title: match[1]?.trim() || `Slide ${index + 1}`,
  }));
  if (h1.length > 1) {
    return h1;
  }
  if (HAS_SECTION.test(normalized)) {
    return packHeadingSlides(normalized, fallbackTitle);
  }
  if (h1.length === 1) {
    return expandLongSlides(h1);
  }
  return expandLongSlides([
    {
      number: 1,
      title: fallbackTitle.trim() || "Lesson",
      content: normalized,
    },
  ]);
}

function splitByHeading(
  markdown: string,
  pattern: RegExp,
  meta: (match: RegExpExecArray, index: number) => { number: number; title: string },
): LessonSlide[] {
  const heading = new RegExp(pattern.source, "gm");
  const matches = [...markdown.matchAll(heading)];
  if (matches.length === 0) {
    return [];
  }
  return matches.map((match, index) => {
    const start = (match.index ?? 0) + match[0].length;
    const end = matches[index + 1]?.index ?? markdown.length;
    const { number, title } = meta(match, index);
    return {
      number,
      title,
      content: markdown.slice(start, end).trim(),
    };
  });
}

type SlideUnit = { heading: string | null; text: string };

function packHeadingSlides(markdown: string, fallbackTitle: string): LessonSlide[] {
  const lines = markdown.split("\n");
  const sections: { title: string; body: string[] }[] = [];
  let current: { title: string; body: string[] } | null = null;
  const preamble: string[] = [];
  let sawH2 = false;
  for (const line of lines) {
    const h2 = line.startsWith("###") ? null : H2_LINE.exec(line);
    const h3 = H3_LINE.exec(line);
    if (h2) {
      sawH2 = true;
      if (current) sections.push(current);
      current = { title: h2[1]?.trim() || fallbackTitle, body: [] };
      continue;
    }
    if (!sawH2 && h3) {
      if (current) sections.push(current);
      current = { title: h3[1]?.trim() || fallbackTitle, body: [] };
      continue;
    }
    if (current) current.body.push(line);
    else preamble.push(line);
  }
  if (current) sections.push(current);

  const slides: LessonSlide[] = [];
  const intro = preamble
    .join("\n")
    .replace(/^#\s+.+$/m, "")
    .trim();
  if (intro) slides.push(...packSection(fallbackTitle.trim() || "Lesson", intro));
  for (const section of sections) {
    const packed = packSection(section.title, section.body.join("\n"));
    if (packed.length > 0) slides.push(...packed);
    else slides.push({ number: 0, title: section.title, sectionTitle: section.title, content: "" });
  }
  return slides.map((slide, index) => ({ ...slide, number: index + 1 }));
}

function packSection(sectionTitle: string, body: string): LessonSlide[] {
  const units = unitsInSection(body);
  if (units.length === 0) return [];
  const groups: SlideUnit[][] = [];
  let current: SlideUnit[] = [];
  let size = 0;
  const flush = () => {
    if (current.length === 0) return;
    groups.push(current);
    current = [];
    size = 0;
  };
  for (const unit of units) {
    const weight = unit.text.length + (unit.heading ? unit.heading.length + 8 : 0);
    if (current.length > 0 && size + weight > SLIDE_TARGET_CHARS) flush();
    current.push(unit);
    size += weight + 2;
  }
  flush();
  return groups.map((group) => ({
    number: 0,
    title: sectionTitle,
    sectionTitle,
    content: group
      .map((unit) => (unit.heading ? `### ${unit.heading}\n\n${unit.text}`.trim() : unit.text))
      .filter(Boolean)
      .join("\n\n"),
  }));
}

function unitsInSection(body: string): SlideUnit[] {
  const parts = body.split(/^(?=###\s+)/m);
  const units: SlideUnit[] = [];
  for (const part of parts) {
    const heading = /^###\s+([^\n]+)\n?/.exec(part);
    const text = (heading ? part.slice(heading[0].length) : part).trim();
    const title = heading?.[1]?.trim() ?? null;
    if (!text) continue;
    if (isProtectedBlock(text) || text.length <= SLIDE_TARGET_CHARS) {
      units.push({ heading: title, text });
      continue;
    }
    const chunks = chunkPreservingBlocks(text, SLIDE_TARGET_CHARS);
    chunks.forEach((chunk, index) => {
      units.push({ heading: index === 0 ? title : null, text: chunk });
    });
  }
  return units;
}

function isProtectedBlock(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed.startsWith("$$") && trimmed.endsWith("$$") && !trimmed.slice(2, -2).includes("$$")) {
    return true;
  }
  const lines = trimmed
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.length > 0 && lines.every((line) => isTableLine(line));
}

function expandLongSlides(slides: LessonSlide[]): LessonSlide[] {
  const expanded = slides.flatMap((slide) => splitLongSlide(slide));
  return expanded.map((slide, index) => ({ ...slide, number: index + 1 }));
}

function splitLongSlide(slide: LessonSlide): LessonSlide[] {
  if (slide.content.length <= SLIDE_TARGET_CHARS) return [slide];
  const chunks = chunkPreservingBlocks(slide.content, SLIDE_TARGET_CHARS);
  if (chunks.length <= 1) return [slide];
  return chunks.map((content, index) => ({
    number: slide.number,
    title: index === 0 ? slide.title : `${slide.title} (${index + 1})`,
    content,
  }));
}

/** Paragraph groups, leaving tables, fences, and display math intact. */
function chunkPreservingBlocks(markdown: string, limit: number): string[] {
  const blocks = atomicBlocks(markdown);
  const chunks: string[][] = [];
  let current: string[] = [];
  let size = 0;
  for (const block of blocks) {
    if (current.length > 0 && size >= limit) {
      chunks.push(current);
      current = [];
      size = 0;
    }
    current.push(block);
    size += block.length + 2;
  }
  if (current.length > 0) chunks.push(current);
  return chunks.map((parts) => parts.join("\n\n"));
}

function atomicBlocks(markdown: string): string[] {
  const lines = markdown.split("\n");
  const blocks: string[] = [];
  let buffer: string[] = [];
  let mode: "prose" | "fence" | "math" | "table" = "prose";

  function flush(): void {
    const text = buffer.join("\n").trim();
    buffer = [];
    if (text) blocks.push(text);
  }

  for (const line of lines) {
    if (mode === "fence") {
      buffer.push(line);
      if (line.trim().startsWith("```") && buffer.length > 1) {
        mode = "prose";
        flush();
      }
      continue;
    }
    if (mode === "math") {
      buffer.push(line);
      if (line.includes("$$")) {
        mode = "prose";
        flush();
      }
      continue;
    }
    if (mode === "table") {
      if (isTableLine(line)) {
        buffer.push(line);
        continue;
      }
      flush();
      mode = "prose";
    }
    if (line.trim() === "") {
      flush();
      continue;
    }
    if (line.trim().startsWith("```")) {
      flush();
      buffer.push(line);
      mode = "fence";
      continue;
    }
    if (isTableLine(line)) {
      flush();
      buffer.push(line);
      mode = "table";
      continue;
    }
    const dollars = line.match(/\$\$/g)?.length ?? 0;
    if (dollars % 2 === 1) {
      flush();
      buffer.push(line);
      mode = "math";
      continue;
    }
    buffer.push(line);
  }
  flush();
  return blocks;
}

function isTableLine(line: string): boolean {
  return /^\s*\|.*\|\s*$/.test(line);
}
