import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { updateTopicMaterialProgress } from "../api/materials";
import { ApiError } from "../api/types";
import { Crumbs } from "../components/Crumbs";
import { LessonSlideStage } from "../components/LessonSlideStage";
import { PushButton } from "../components/PushButton";
import { slidesForLessonView } from "../lib/lessonSlides";
import { quizActionLabel } from "../lib/quizAction";
import { topicPagePath } from "../lib/subjectMaterial";
import { useTopicLesson } from "../lib/useTopicLesson";
import { resumeSlideIndex } from "../lib/useSubtopicLesson";
import { clearLearningDirectoryCache } from "./HomePage";

export function TopicLessonPage() {
  const {
    subjectId,
    topicId,
    lesson,
    setLesson,
    subjectName,
    topicTitle,
    quizSummary,
    error,
    subjectPath,
  } = useTopicLesson();
  const [searchParams] = useSearchParams();
  const [slideIndex, setSlideIndex] = useState(0);
  const [progressError, setProgressError] = useState<string | null>(null);
  const positioned = useRef(false);
  const savedKey = useRef<string | null>(null);

  const slides = lesson ? slidesForLessonView(lesson) : [];
  const slide = slides[slideIndex];
  const atEnd = slides.length > 0 && slideIndex >= slides.length - 1;

  useEffect(() => {
    positioned.current = false;
    savedKey.current = null;
    setSlideIndex(0);
  }, [topicId]);

  useEffect(() => {
    if (!lesson || positioned.current) return;
    const nextSlides = slidesForLessonView(lesson);
    if (nextSlides.length === 0) return;
    positioned.current = true;
    const fromStart = searchParams.get("from") === "start";
    setSlideIndex(fromStart ? 0 : resumeSlideIndex(lesson, nextSlides));
  }, [lesson, searchParams]);

  useEffect(() => {
    if (!lesson) return;
    const nextSlides = slidesForLessonView(lesson);
    if (nextSlides.length === 0) return;
    const unit = nextSlides[slideIndex]?.number ?? slideIndex + 1;
    const onLast = slideIndex >= nextSlides.length - 1;
    const status =
      onLast || lesson.progress?.status === "completed" ? "completed" : "opened";
    const key = `${topicId}:${status}:${unit}`;
    if (savedKey.current === key) return;
    if (
      lesson.progress?.status === status &&
      lesson.progress.last_unit_ordinal === unit
    ) {
      savedKey.current = key;
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const progress = await updateTopicMaterialProgress(topicId, {
          status,
          last_unit_ordinal: unit,
        });
        if (cancelled) return;
        savedKey.current = key;
        if (status === "completed") clearLearningDirectoryCache();
        setLesson((prev) =>
          prev
            ? {
                ...prev,
                progress,
                quiz_unlocked:
                  status === "completed"
                    ? Boolean(prev.quiz_id) || prev.quiz_unlocked
                    : prev.quiz_unlocked,
              }
            : prev,
        );
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 403) return;
        setProgressError(err instanceof ApiError ? err.message : "Could not save lesson progress.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [lesson, setLesson, slideIndex, topicId]);

  const quizUnlocked = Boolean(lesson?.quiz_unlocked && lesson.quiz_id);
  const quizId = lesson?.quiz_id ?? quizSummary?.id ?? null;
  const quizCta = quizActionLabel(quizSummary);
  const topicPath = topicPagePath(subjectId, topicId);

  return (
    <>
      {!lesson && !error && (
        <div className="center-state" role="status">
          Loading topic lesson…
        </div>
      )}

      {error && (
        <div className="center-state">
          <p className="form__error" role="alert">
            {error}
          </p>
          <div className="actions" style={{ justifyContent: "center" }}>
            <Link to={subjectPath} className="btn btn--soft btn--sm">
              Back to subject
            </Link>
          </div>
        </div>
      )}

      {lesson && slide && (
        <div className="lesson-view topic-lesson-page">
          <Crumbs
            parts={[
              { label: "Subjects", to: "/" },
              { label: subjectName, to: subjectPath },
              { label: topicTitle },
            ]}
          />
          <h1 className="sr-only">{topicTitle}</h1>
          <LessonSlideStage
            slides={slides}
            index={slideIndex}
            onIndexChange={setSlideIndex}
            ariaLabel={topicTitle}
            complete={lesson.progress?.status === "completed"}
            leading={
              <Link to={topicPath} className="lesson-back">
                Back to topic
              </Link>
            }
            endAction={
              quizId ? (
                quizUnlocked ? (
                  <Link to={`/quizzes/${quizId}`} className="btn btn--sm">
                    {quizCta}
                  </Link>
                ) : (
                  <PushButton size="sm" disabled>
                    Start quiz
                  </PushButton>
                )
              ) : undefined
            }
            hint={
              atEnd ? (
                <p className="lesson-toolbar__hint">
                  {quizUnlocked
                    ? "Topic quiz follows the slides."
                    : "Complete the topic lesson first."}
                </p>
              ) : null
            }
            after={
              progressError ? (
                <p className="form__error" role="alert">
                  {progressError}
                </p>
              ) : null
            }
          />
        </div>
      )}
    </>
  );
}
