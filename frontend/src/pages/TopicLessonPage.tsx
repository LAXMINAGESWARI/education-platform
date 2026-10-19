import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { updateTopicMaterialProgress } from "../api/materials";
import { ApiError } from "../api/types";
import {
  AttemptHistoryList,
  AttemptHistoryTrigger,
  formatAttempt,
  formatAttemptWhen,
} from "../components/AttemptHistory";
import { Crumbs } from "../components/Crumbs";
import { LessonSlideFrame } from "../components/LessonSlideFrame";
import { PushButton } from "../components/PushButton";
import { slidesForLessonView } from "../lib/lessonSlides";
import { latestRetakeLabel, passedBestLabel, quizActionLabel, trackedAttempts } from "../lib/quizAction";
import { useTopicLesson } from "../lib/useTopicLesson";
import { resumeSlideIndex } from "../lib/useSubtopicLesson";
import { clearLearningDirectoryCache } from "./HomePage";

export function TopicLessonPage() {
  const {
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
  const [showHistory, setShowHistory] = useState(false);
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
  const attempts = trackedAttempts(quizSummary?.recent_attempts ?? []);
  const latestAttempt = attempts[0] ?? null;
  const quizCta = quizActionLabel(quizSummary);
  const latestWhen = latestAttempt ? formatAttemptWhen(latestAttempt) : null;
  const passedLabel = passedBestLabel(quizSummary);
  const retakeLabel = latestRetakeLabel(quizSummary);

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

          <div className="lesson-layout">
            <section className="lesson-layout__main lesson-slides" aria-label={topicTitle}>
              <LessonSlideFrame
                title={slide.title}
                content={slide.content}
                ariaLabel={topicTitle}
                footer={
                  <>
                    <div className="slide-nav__controls">
                      <PushButton
                        variant="outline"
                        size="sm"
                        disabled={slideIndex === 0}
                        onClick={() => setSlideIndex((index) => Math.max(0, index - 1))}
                      >
                        Previous
                      </PushButton>
                      {atEnd ? (
                        <span className="lesson-toolbar__hint">Topic quiz follows the slides.</span>
                      ) : (
                        <PushButton
                          size="sm"
                          onClick={() =>
                            setSlideIndex((index) => Math.min(slides.length - 1, index + 1))
                          }
                        >
                          Next slide
                        </PushButton>
                      )}
                    </div>
                    <p className="slide-nav__status">
                      Slide {slideIndex + 1} of {slides.length}
                      {lesson.progress?.status === "completed" ? " · complete" : ""}
                    </p>
                  </>
                }
              />
              {progressError && (
                <p className="form__error" role="alert">
                  {progressError}
                </p>
              )}
            </section>

            <aside className="lesson-layout__aside" aria-label="Topic quiz" id="quiz">
              <div className="lesson-quiz-panel">
                <div className="lesson-quiz-panel__hero">
                  <div>
                    <h2 className="lesson-quiz-panel__title">Topic quiz</h2>
                    <div className="lesson-quiz-panel__meta">
                      {passedLabel ? (
                        <span className="badge badge--ok">{passedLabel}</span>
                      ) : quizUnlocked ? (
                        <span className="badge badge--info">Unlocked</span>
                      ) : (
                        <span className="badge badge--locked">Locked</span>
                      )}
                      <span className="lesson-toolbar__hint">
                        {quizSummary
                          ? `${quizSummary.attempt_count} attempt${quizSummary.attempt_count === 1 ? "" : "s"}`
                          : "No quiz linked"}
                      </span>
                    </div>
                  </div>
                  <div className="lesson-quiz-panel__actions">
                    {quizUnlocked && quizId ? (
                      <Link to={`/quizzes/${quizId}`} className="btn btn--sm">
                        {quizCta}
                      </Link>
                    ) : (
                      <button type="button" className="btn btn--sm" disabled>
                        Start quiz
                      </button>
                    )}
                    {!quizUnlocked && (
                      <p className="lesson-toolbar__hint">Complete the topic lesson first.</p>
                    )}
                  </div>
                </div>

                <div className="lesson-quiz-panel__latest">
                  <h3>Last attempt</h3>
                  {latestAttempt ? (
                    <div className="lesson-quiz-panel__latest-row">
                      <p>
                        <strong>{retakeLabel ?? formatAttempt(latestAttempt)}</strong>
                        {!retakeLabel && latestWhen && (
                          <span className="lesson-toolbar__hint"> · {latestWhen}</span>
                        )}
                        {!retakeLabel && quizSummary?.best_score_percent != null && (
                          <span className="lesson-toolbar__hint">
                            {" "}
                            · Best {Math.round(Number(quizSummary.best_score_percent))}%
                          </span>
                        )}
                      </p>
                      <Link to={`/attempts/${latestAttempt.id}`} className="btn btn--soft btn--sm">
                        View attempt
                      </Link>
                    </div>
                  ) : (
                    <p className="lesson-toolbar__hint">No attempts yet.</p>
                  )}
                </div>

                <AttemptHistoryTrigger
                  title="Show full history"
                  attempts={attempts}
                  active={showHistory}
                  onOpen={() => setShowHistory(true)}
                />
                {showHistory && <AttemptHistoryList attempts={attempts} />}
              </div>
            </aside>
          </div>
        </div>
      )}
    </>
  );
}
