import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { useParams } from "react-router-dom";

import { fetchLearningDirectory, getSubtopicMaterial, updateMaterialProgress } from "../api/materials";
import type { LessonMaterial, LessonSlide, QuizSummary } from "../api/types";
import { ApiError } from "../api/types";

export { findSummarySlide } from "./lessonSlides";

type CachedSubtopicLesson = {
  lesson: LessonMaterial;
  subjectName: string;
  subtopicTitle: string;
  quizSummary: QuizSummary | null;
};

const cache = new Map<string, CachedSubtopicLesson>();

function cacheKey(subjectId: string, subtopicId: string): string {
  return `${subjectId}:${subtopicId}`;
}

export function clearSubtopicLessonCache(): void {
  cache.clear();
}

/** Map a saved 1-based slide number onto the slides for this lesson. */
export function slideIndexFromOrdinal(
  slides: { number: number }[],
  ordinal: number | null | undefined,
): number {
  if (ordinal == null || !Number.isFinite(ordinal) || ordinal <= 1 || slides.length === 0) return 0;
  const match = slides.findIndex((slide) => slide.number === ordinal);
  if (match >= 0) return match;
  return Math.min(slides.length - 1, Math.floor(ordinal) - 1);
}

export function resumeSlideIndex(lesson: LessonMaterial, slides: LessonSlide[]): number {
  return slideIndexFromOrdinal(slides, lesson.progress?.last_unit_ordinal);
}

export function useSubtopicLesson() {
  const { subjectId = "", subtopicId = "" } = useParams();
  const key = cacheKey(subjectId, subtopicId);
  const cached = cache.get(key);
  const [lesson, setLessonState] = useState<LessonMaterial | null>(cached?.lesson ?? null);
  const [subjectName, setSubjectName] = useState(cached?.subjectName ?? "Subject");
  const [subtopicTitle, setSubtopicTitle] = useState(cached?.subtopicTitle ?? "Lesson");
  const [quizSummary, setQuizSummary] = useState<QuizSummary | null>(cached?.quizSummary ?? null);
  const [error, setError] = useState<string | null>(null);
  const openedRef = useRef(Boolean(cached));

  const subjectPath = `/subjects/${subjectId}`;
  const lessonPath = `${subjectPath}/subtopics/${subtopicId}/lesson`;

  const setLesson: Dispatch<SetStateAction<LessonMaterial | null>> = useCallback(
    (update) => {
      setLessonState((prev) => {
        const next = typeof update === "function" ? update(prev) : update;
        const current = cache.get(key);
        if (next == null) {
          cache.delete(key);
        } else {
          cache.set(key, {
            lesson: next,
            subjectName: current?.subjectName ?? "Subject",
            subtopicTitle: current?.subtopicTitle ?? "Lesson",
            quizSummary: current?.quizSummary ?? null,
          });
        }
        return next;
      });
    },
    [key],
  );

  useEffect(() => {
    let cancelled = false;
    const hit = cache.get(key);
    if (hit) {
      setLessonState(hit.lesson);
      setSubjectName(hit.subjectName);
      setSubtopicTitle(hit.subtopicTitle);
      setQuizSummary(hit.quizSummary);
      setError(null);
      openedRef.current = true;
    } else {
      openedRef.current = false;
      setLessonState(null);
      setQuizSummary(null);
      setError(null);
    }

    void (async () => {
      try {
        const [data, directory] = await Promise.all([
          getSubtopicMaterial(subtopicId),
          fetchLearningDirectory(),
        ]);
        if (cancelled) return;
        const subject = directory.subjects.find((item) => item.id === subjectId);
        let subtopic = null;
        for (const topic of subject?.topics ?? []) {
          const found = topic.subtopics.find((item) => item.id === subtopicId);
          if (found) {
            subtopic = found;
            break;
          }
        }
        const nextSubjectName = subject?.name ?? "Subject";
        const nextSubtopicTitle = subtopic?.title ?? data.title;
        const nextQuiz = subtopic?.quiz ?? null;
        cache.set(key, {
          lesson: data,
          subjectName: nextSubjectName,
          subtopicTitle: nextSubtopicTitle,
          quizSummary: nextQuiz,
        });
        setSubjectName(nextSubjectName);
        setSubtopicTitle(nextSubtopicTitle);
        setQuizSummary(nextQuiz);
        setLessonState(data);
        if (!openedRef.current) {
          openedRef.current = true;
          const progress = await updateMaterialProgress(subtopicId, {
            status: data.progress?.status === "completed" ? "completed" : "opened",
            last_unit_ordinal: data.progress?.last_unit_ordinal ?? 1,
          });
          if (!cancelled) {
            setLesson((prev) => (prev ? { ...prev, progress } : prev));
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load lesson.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [key, subtopicId, subjectId, setLesson]);

  return {
    subjectId,
    subtopicId,
    subjectPath,
    lessonPath,
    lesson,
    setLesson,
    subjectName,
    subtopicTitle,
    quizSummary,
    error,
    setError,
    slides: lesson?.slides ?? [],
  };
}
