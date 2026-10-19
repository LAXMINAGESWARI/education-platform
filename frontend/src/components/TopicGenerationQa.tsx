import { useMemo, useState } from "react";

import type {
  GenerationQaItem,
  ItemValidationStats,
  LessonMaterial,
  RunPhase,
} from "../api/types";
import { slidesForLessonView } from "../lib/lessonSlides";
import { LessonSlideFrame } from "./LessonSlideFrame";
import { PushButton } from "./PushButton";
import { QuizItemCards } from "./QuizItemCards";
import "./TopicGenerationQa.css";

export type QaReviewPhase = Extract<RunPhase, "qa_review" | "published">;

export type TopicGenerationQaProps = {
  items: GenerationQaItem[];
  draftLessonMarkdown: string | null;
  publishedLessonVersionId?: string | null;
  publishedQuizVersionId?: string | null;
  phase: QaReviewPhase;
  busy: boolean;
  canPublish: boolean;
  canReject: boolean;
  onRejectSelected: (questionIds: string[]) => void;
  onRequestPublish: () => void;
  itemValidation?: ItemValidationStats | null;
};

function validationSummary(stats: ItemValidationStats | null | undefined): string | null {
  if (stats == null) return null;
  return `${stats.passed} passed · ${stats.fixed_on_retry} fixed on retry · ${stats.dropped} dropped`;
}

function phaseHeadline(phase: QaReviewPhase): string {
  switch (phase) {
    case "qa_review":
      return "Review the draft lesson and item bank. Request changes on sections or items, then an administrator publishes.";
    case "published":
      return "This run is published. Students can open the topic lesson and topic quiz.";
    default: {
      const _never: never = phase;
      return _never;
    }
  }
}

function draftLessonMaterial(markdown: string): LessonMaterial {
  return {
    id: "draft-lesson",
    title: "Draft lesson",
    markdown,
    slides: [],
    source_material_version_id: "",
    progress: null,
    quiz_unlocked: false,
    quiz_id: null,
  };
}

export function TopicGenerationQa({
  items,
  draftLessonMarkdown,
  publishedLessonVersionId,
  publishedQuizVersionId,
  phase,
  busy,
  canPublish,
  canReject,
  onRejectSelected,
  onRequestPublish,
  itemValidation = null,
}: TopicGenerationQaProps) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const sortedItems = useMemo(
    () => [...items].sort((left, right) => left.sequence - right.sequence),
    [items],
  );
  const slides = useMemo(
    () =>
      draftLessonMarkdown
        ? slidesForLessonView(draftLessonMaterial(draftLessonMarkdown))
        : [],
    [draftLessonMarkdown],
  );
  const selectedCount = sortedItems.filter((item) => selected.has(item.question_id)).length;
  const allSelected = sortedItems.length > 0 && selectedCount === sortedItems.length;
  const publishEnabled = canPublish && phase === "qa_review" && !busy;
  const rejectEnabled = canReject && phase === "qa_review" && !busy && selectedCount > 0;
  const showActions = phase === "qa_review" && (canPublish || canReject);

  function toggle(questionId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(questionId)) next.delete(questionId);
      else next.add(questionId);
      return next;
    });
  }

  function toggleAll() {
    if (allSelected) {
      setSelected(new Set());
      return;
    }
    setSelected(new Set(sortedItems.map((item) => item.question_id)));
  }

  function rejectSelected() {
    const ids = sortedItems
      .filter((item) => selected.has(item.question_id))
      .map((item) => item.question_id);
    if (ids.length === 0) return;
    onRejectSelected(ids);
    setSelected(new Set());
  }

  return (
    <div className="qa-review lesson-view">
      <p className="muted">{phaseHeadline(phase)}</p>
      {phase === "published" ? (
        <p className="qa-review__published" role="status">
          Published lesson {publishedLessonVersionId ?? "version"} · quiz{" "}
          {publishedQuizVersionId ?? "version"}
        </p>
      ) : null}
      <div className="lesson-layout">
        <section className="lesson-layout__main" aria-label="Draft lesson">
          {slides.length === 0 ? (
            <p className="muted">No draft lesson markdown on this run.</p>
          ) : (
            <div className="qa-review__slides">
              {slides.map((slide) => (
                <LessonSlideFrame
                  key={slide.number}
                  title={slide.title}
                  content={slide.content}
                  ariaLabel={`Slide ${slide.number}: ${slide.title}`}
                />
              ))}
            </div>
          )}
        </section>
        <aside className="lesson-layout__aside" aria-label="Draft quiz">
          <div className="lesson-quiz-panel">
            <div className="lesson-quiz-panel__hero">
              <div>
                <h2 className="lesson-quiz-panel__title">Topic quiz</h2>
                <div className="lesson-quiz-panel__meta">
                  <span className="lesson-toolbar__hint">
                    {sortedItems.length === 0
                      ? "No items in the draft bank yet."
                      : sortedItems.length === 1
                        ? "1 question · item bank"
                        : `${sortedItems.length} questions · item bank`}
                  </span>
                  {validationSummary(itemValidation) ? (
                    <span className="lesson-toolbar__hint">{validationSummary(itemValidation)}</span>
                  ) : null}
                </div>
              </div>
              {showActions ? (
                <div className="lesson-quiz-panel__actions admin-upload__actions">
                  {canReject ? (
                    <PushButton
                      type="button"
                      variant="outline"
                      disabled={!rejectEnabled}
                      loading={busy}
                      onClick={rejectSelected}
                    >
                      Reject selected items
                    </PushButton>
                  ) : null}
                  {canPublish ? (
                    <PushButton
                      type="button"
                      disabled={!publishEnabled}
                      loading={busy}
                      onClick={onRequestPublish}
                    >
                      Publish to students
                    </PushButton>
                  ) : null}
                </div>
              ) : null}
            </div>
            {sortedItems.length === 0 ? (
              <p className="lesson-toolbar__hint">No items in the draft bank yet.</p>
            ) : (
              <QuizItemCards
                items={sortedItems}
                canSelect={canReject && phase === "qa_review"}
                busy={busy}
                selected={selected}
                allSelected={allSelected}
                onToggle={toggle}
                onToggleAll={toggleAll}
              />
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
