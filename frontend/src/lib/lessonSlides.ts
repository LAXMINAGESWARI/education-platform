import type { LessonMaterial, LessonSlide } from "../api/types";
import { prepareLessonMarkdown } from "./markdownMath";

const CANONICAL_SLIDE = /^## Slide (\d+)\s*[—\-]\s*(.+?)\s*$/;
const H1_HEADING = /^#\s+(.+?)\s*$/;
const H2_HEADING = /^##\s+(.+?)\s*$/;

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
  const h2 = splitByHeading(normalized, H2_HEADING, (match, index) => ({
    number: index + 1,
    title: match[1]?.trim() || `Slide ${index + 1}`,
  }));
  if (h2.length > 0) {
    return h2;
  }
  if (h1.length === 1) {
    return h1;
  }
  return [
    {
      number: 1,
      title: fallbackTitle.trim() || "Lesson",
      content: normalized,
    },
  ];
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
