import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchLearningDirectory, getTopicMaterial, updateTopicMaterialProgress } from "../api/materials";
import type { LessonMaterial, QuizSummary } from "../api/types";
import { ApiError } from "../api/types";
import { clearLearningDirectoryCache } from "../pages/HomePage";
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
  const [lesson, setLesson] = useState<LessonMaterial | null>(cached?.lesson ?? null);
  const [subjectName, setSubjectName] = useState(cached?.subjectName ?? "Subject");
  const [topicTitle, setTopicTitle] = useState(cached?.topicTitle ?? "Topic lesson");
  const [quizSummary, setQuizSummary] = useState<QuizSummary | null>(cached?.quizSummary ?? null);
  const [error, setError] = useState<string | null>(null);

  const subjectPath = `/subjects/${subjectId}`;
  const lessonPath = topicLessonPath(subjectId, topicId);

  useEffect(() => {
    let cancelled = false;
    const hit = cache.get(key);
    if (hit) {
      setLesson(hit.lesson);
      setSubjectName(hit.subjectName);
      setTopicTitle(hit.topicTitle);
      setQuizSummary(hit.quizSummary);
      setError(null);
    } else {
      setLesson(null);
      setQuizSummary(null);
      setError(null);
    }

    void (async () => {
      try {
        let data = await getTopicMaterial(topicId);
        if (cancelled) return;
        if (data.progress?.status !== "completed") {
          try {
            await updateTopicMaterialProgress(topicId, { status: "completed" });
            data = await getTopicMaterial(topicId);
            clearLearningDirectoryCache();
          } catch (progressError) {
            if (!(progressError instanceof ApiError) || progressError.status !== 403) {
              throw progressError;
            }
          }
        }
        const directory = await fetchLearningDirectory();
        if (cancelled) return;
        const found = findSubjectTopic(directory, subjectId, topicId);
        const nextSubjectName = found?.subject.name ?? "Subject";
        const nextTopicTitle = data.title || found?.topic.title || "Topic lesson";
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
        setLesson(data);
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
    subjectName,
    topicTitle,
    quizSummary,
    error,
  };
}
