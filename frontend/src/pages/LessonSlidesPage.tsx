import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { updateMaterialProgress } from "../api/materials";
import { recordSlideEngagement } from "../api/engagement";
import { ApiError } from "../api/types";
import { Crumbs } from "../components/Crumbs";
import { MarkdownContent } from "../components/MarkdownContent";
import { PushButton } from "../components/PushButton";
import { quizActionLabel } from "../lib/quizAction";
import { resumeSlideIndex, useSubtopicLesson } from "../lib/useSubtopicLesson";

export function LessonSlidesPage() {
  const {
    lesson,
    setLesson,
    subjectName,
    subtopicTitle,
    error,
    setError,
    slides,
    subjectPath,
    lessonPath,
    subtopicId,
    quizSummary,
  } = useSubtopicLesson();
  const [searchParams] = useSearchParams();
  const [slideIndex, setSlideIndex] = useState(0);
  const startedRef = useRef(false);

  // ─── Slide engagement tracking ────────────────────────────────────────────
  // Tracks active seconds per slide (pauses when tab is hidden).
  const activeSecondsRef = useRef<number>(0);   // accumulated active time for current slide
  const lastTickRef      = useRef<number>(Date.now()); // when we last "ticked"
  const isVisibleRef     = useRef<boolean>(true);      // is the tab currently visible?
  const slideIndexRef    = useRef<number>(0);           // mirror of slideIndex for use in effects/unmount

  // Keep slideIndexRef in sync
  useEffect(() => {
    slideIndexRef.current = slideIndex;
  }, [slideIndex]);

  // Page Visibility API — pause the clock when tab is hidden
  useEffect(() => {
    function handleVisibility() {
      if (document.hidden) {
        // Tab hidden — bank time up to now
        activeSecondsRef.current += (Date.now() - lastTickRef.current) / 1000;
        isVisibleRef.current = false;
      } else {
        // Tab visible again — restart the tick
        lastTickRef.current = Date.now();
        isVisibleRef.current = true;
      }
    }
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, []);

  // Reset timer whenever slide changes
  useEffect(() => {
    activeSecondsRef.current = 0;
    lastTickRef.current = Date.now();
    isVisibleRef.current = !document.hidden;
  }, [slideIndex]);

  // Flush on unmount (student navigates away mid-lesson)
  useEffect(() => {
    return () => {
      const extra = isVisibleRef.current
        ? (Date.now() - lastTickRef.current) / 1000
        : 0;
      const total = activeSecondsRef.current + extra;
      if (subtopicId && total >= 2) {
        void recordSlideEngagement(subtopicId, slideIndexRef.current, total);
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subtopicId]);

  /**
   * Flush active time for the given slide index to the backend.
   * Call this BEFORE changing slideIndex.
   */
  async function flushEngagement(index: number) {
    if (!subtopicId) return;
    const extra = isVisibleRef.current
      ? (Date.now() - lastTickRef.current) / 1000
      : 0;
    const total = activeSecondsRef.current + extra;
    if (total < 2) return; // ignore sub-2s noise
    try {
      await recordSlideEngagement(subtopicId, index, total);
    } catch {
      /* non-blocking — never interrupt the student's flow */
    }
  }
  // ──────────────────────────────────────────────────────────────────────────

  useEffect(() => {
    startedRef.current = false;
    setSlideIndex(0);
  }, [subtopicId]);

  useEffect(() => {
    if (!lesson || startedRef.current) return;
    startedRef.current = true;
    const fromStart = searchParams.get("from") === "start";
    setSlideIndex(fromStart ? 0 : resumeSlideIndex(lesson, lesson.slides));
  }, [lesson, searchParams]);

  const slide = slides[slideIndex];
  const atEnd = slides.length > 0 && slideIndex === slides.length - 1;
  const lessonComplete = Boolean(lesson?.progress?.status === "completed");
  const quizId = lesson?.quiz_id ?? quizSummary?.id ?? null;
  const quizUnlocked = Boolean(lesson?.quiz_unlocked && quizId);

  async function markCompleted(unit: number) {
    try {
      const progress = await updateMaterialProgress(subtopicId, {
        status: "completed",
        last_unit_ordinal: unit,
      });
      setLesson((prev) =>
        prev
          ? {
              ...prev,
              progress,
              quiz_unlocked: Boolean(prev.quiz_id) || prev.quiz_unlocked,
            }
          : prev,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save lesson progress.");
    }
  }

  async function persistSlidePosition(index: number) {
    if (!lesson || !slides.length) return;
    const unit = slides[index]?.number ?? index + 1;
    try {
      const progress = await updateMaterialProgress(subtopicId, {
        status:
          lesson.progress?.status === "completed" || index === slides.length - 1
            ? "completed"
            : "opened",
        last_unit_ordinal: unit,
      });
      setLesson((prev) => (prev ? { ...prev, progress } : prev));
    } catch {
      /* non-blocking */
    }
  }

  async function goNext() {
    if (!lesson || !slides.length) return;
    const next = Math.min(slides.length - 1, slideIndex + 1);
    // Flush engagement for the slide we're leaving
    await flushEngagement(slideIndex);
    setSlideIndex(next);
    if (next === slides.length - 1) {
      await markCompleted(slides[next]?.number ?? next + 1);
    } else {
      await persistSlidePosition(next);
    }
  }

  async function goPrev() {
    const prev = Math.max(0, slideIndex - 1);
    // Flush engagement for the slide we're leaving
    await flushEngagement(slideIndex);
    setSlideIndex(prev);
    void persistSlidePosition(prev);
  }

  return (
    <>
      {!lesson && !error && (
        <div className="center-state" role="status">
          Loading lesson…
        </div>
      )}

      {error && (
        <div className="center-state">
          <p className="form__error" role="alert">
            {error}
          </p>
        </div>
      )}

      {lesson && slide && (
        <div className="lesson-view">
          <Crumbs
            parts={[
              { label: "Subjects", to: "/" },
              { label: subjectName, to: subjectPath },
              { label: subtopicTitle, to: lessonPath },
              { label: "Slides" },
            ]}
          />

          <div className="lesson-slides">
            <article className="panel lesson-overview">
              <div className="lesson-overview__header">
                <h2>{slide.title}</h2>
              </div>
              <div className="lesson-overview__scroll markdown">
                <MarkdownContent>{slide.content}</MarkdownContent>
              </div>
              <div className="lesson-overview__footer">
                <div className="slide-nav__controls">
                  <PushButton
                    variant="outline"
                    size="sm"
                    disabled={slideIndex === 0}
                    onClick={() => void goPrev()}
                  >
                    Previous
                  </PushButton>
                  {atEnd && quizId ? (
                    quizUnlocked ? (
                      <Link to={`/quizzes/${quizId}`} className="btn btn--sm">
                        {quizActionLabel(quizSummary)}
                      </Link>
                    ) : (
                      <PushButton size="sm" disabled>
                        Start quiz
                      </PushButton>
                    )
                  ) : (
                    <PushButton size="sm" disabled={atEnd} onClick={() => void goNext()}>
                      Next slide
                    </PushButton>
                  )}
                </div>
                <p className="slide-nav__status">
                  Slide {slideIndex + 1} of {slides.length}
                  {lessonComplete ? " · complete" : ""}
                </p>
              </div>
            </article>
          </div>
        </div>
      )}
    </>
  );
}
