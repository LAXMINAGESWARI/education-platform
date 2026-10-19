import type { LessonMaterial, LessonSlide } from "../api/types";
import { prepareLessonMarkdown } from "./markdownMath";

const CANONICAL_SLIDE = /^## Slide (\d+)\s*[—\-]\s*(.+?)\s*$/;
const H1_HEADING = /^#\s+(.+?)\s*$/;
const SECTION_HEADING = /^#{2,3}\s+(.+?)\s*$/;
const LONG_SECTION_CHARS = 1800;

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
  const sections = splitByHeading(normalized, SECTION_HEADING, (match, index) => ({
    number: index + 1,
    title: match[1]?.trim() || `Slide ${index + 1}`,
  }));
  if (sections.length > 0) {
    return expandLongSlides(sections);
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

function expandLongSlides(slides: LessonSlide[]): LessonSlide[] {
  const expanded = slides.flatMap((slide) => splitLongSlide(slide));
  return expanded.map((slide, index) => ({ ...slide, number: index + 1 }));
}

function splitLongSlide(slide: LessonSlide): LessonSlide[] {
  if (slide.content.length <= LONG_SECTION_CHARS) return [slide];
  const chunks = chunkPreservingBlocks(slide.content, LONG_SECTION_CHARS);
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
