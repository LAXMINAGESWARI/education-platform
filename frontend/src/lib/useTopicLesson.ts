import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { useParams } from "react-router-dom";

import { fetchLearningDirectory, getTopicMaterial } from "../api/materials";
import type { LessonMaterial, QuizSummary } from "../api/types";
import { ApiError } from "../api/types";
import { findSubjectTopic, topicLessonPath } from "./subjectMaterial";

type CachedTopicLesson = {
  lesson: LessonMaterial;
  subjectName: string;
  topicTitle: string;
  quizSummary: QuizSummary | null;
};

const cache = new Map<string, CachedTopicLesson>();

function cacheKey(subjectId: string, topicId: string): string {
  return `${subjectId}:${topicId}`;
}

export function clearTopicLessonCache(): void {
  cache.clear();
}

export function useTopicLesson() {
  const { subjectId = "", topicId = "" } = useParams();
  const key = cacheKey(subjectId, topicId);
  const cached = cache.get(key);
  const [lesson, setLessonState] = useState<LessonMaterial | null>(cached?.lesson ?? null);
  const [subjectName, setSubjectName] = useState(cached?.subjectName ?? "Subject");
  const [topicTitle, setTopicTitle] = useState(cached?.topicTitle ?? "Topic lesson");
  const [quizSummary, setQuizSummary] = useState<QuizSummary | null>(cached?.quizSummary ?? null);
  const [error, setError] = useState<string | null>(null);

  const subjectPath = `/subjects/${subjectId}`;
  const lessonPath = topicLessonPath(subjectId, topicId);

  const setLesson: Dispatch<SetStateAction<LessonMaterial | null>> = useCallback(
    (update) => {
      setLessonState((prev) => {
        const next = typeof update === "function" ? update(prev) : update;
        const current = cache.get(key);
        if (next == null) {
          cache.delete(key);
        } else if (current) {
          cache.set(key, { ...current, lesson: next });
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
      setTopicTitle(hit.topicTitle);
      setQuizSummary(hit.quizSummary);
      setError(null);
    } else {
      setLessonState(null);
      setQuizSummary(null);
      setError(null);
    }

    void (async () => {
      try {
        const data = await getTopicMaterial(topicId);
        if (cancelled) return;
        const directory = await fetchLearningDirectory();
        if (cancelled) return;
        const found = findSubjectTopic(directory, subjectId, topicId);
        const nextSubjectName = found?.subject.name ?? "Subject";
        const nextTopicTitle = found?.topic.title || data.title || "Topic";
        const nextQuiz = found?.topic.overall_quiz ?? null;
        cache.set(key, {
          lesson: data,
          subjectName: nextSubjectName,
          topicTitle: nextTopicTitle,
          quizSummary: nextQuiz,
        });
        setSubjectName(nextSubjectName);
        setTopicTitle(nextTopicTitle);
        setQuizSummary(nextQuiz);
        setLessonState(data);
      } catch (err) {
        if (!cancelled) {
          const fallback =
            err instanceof ApiError && err.status === 404
              ? "This topic lesson is not published yet."
              : "Could not load topic lesson.";
          setError(err instanceof ApiError ? err.message || fallback : fallback);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [key, subjectId, topicId]);

  return {
    subjectId,
    topicId,
    subjectPath,
    lessonPath,
    lesson,
    setLesson,
    subjectName,
    topicTitle,
    quizSummary,
    error,
  };
}
