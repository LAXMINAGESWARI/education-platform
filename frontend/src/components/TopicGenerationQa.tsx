import { useMemo, useState } from "react";

import type { BloomLevel, GenerationQaItem, LessonMaterial, RunPhase } from "../api/types";
import { slidesForLessonView } from "../lib/lessonSlides";
import { presentQuizText } from "../lib/quizText";
import { LessonSlideFrame } from "./LessonSlideFrame";
import { MarkdownContent } from "./MarkdownContent";
import { PushButton } from "./PushButton";
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
};

function bloomLabel(bloom: BloomLevel | null | undefined): string {
  if (bloom == null) return "Unlabeled";
  switch (bloom) {
    case "remember":
      return "Remember";
    case "understand":
      return "Understand";
    case "apply":
      return "Apply";
    case "analyze":
      return "Analyze";
    default: {
      const _never: never = bloom;
      return _never;
    }
  }
}

function misconceptionText(item: GenerationQaItem): string {
  const labels = item.misconception_labels ?? [];
  if (labels.length === 0) return "None listed";
  return labels.join(" · ");
}

function disputedBadge(item: GenerationQaItem): string | null {
  if (item.key_disputed !== true || item.blind_answer == null || item.blind_answer === "") {
    return null;
  }
  return `Key disputed - solver chose ${item.blind_answer}`;
}

function optionEntries(options: Record<string, string>): [string, string][] {
  return Object.entries(options).sort(([left], [right]) => left.localeCompare(right));
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
              <div className="qa-review__table-wrap">
                <table className="qa-review__table">
                  <caption className="sr-only">Item bank for QA review</caption>
                  <thead>
                    <tr>
                      {canReject && phase === "qa_review" ? (
                        <th scope="col">
                          <label className="qa-review__select">
                            <input
                              type="checkbox"
                              checked={allSelected}
                              disabled={busy}
                              onChange={toggleAll}
                              aria-label="Select all items"
                            />
                            Select
                          </label>
                        </th>
                      ) : null}
                      <th scope="col">#</th>
                      <th scope="col">Prompt</th>
                      <th scope="col">Bloom</th>
                      <th scope="col">Misconceptions</th>
                      <th scope="col">Options</th>
                      <th scope="col">Correct</th>
                      <th scope="col">Rationales</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedItems.map((item) => (
                      <tr key={item.question_id}>
                        {canReject && phase === "qa_review" ? (
                          <td>
                            <input
                              type="checkbox"
                              checked={selected.has(item.question_id)}
                              disabled={busy}
                              onChange={() => toggle(item.question_id)}
                              aria-label={`Select item ${item.sequence}`}
                            />
                          </td>
                        ) : null}
                        <td>{item.sequence}</td>
                        <td>
                          <p className="qa-review__prompt">
                            <MarkdownContent inline>{presentQuizText(item.prompt)}</MarkdownContent>
                          </p>
                          {disputedBadge(item) ? (
                            <p className="qa-review__disputed">
                              <span className="badge badge--warn">{disputedBadge(item)}</span>
                            </p>
                          ) : null}
                        </td>
                        <td>
                          <span className="qa-review__bloom">{bloomLabel(item.bloom)}</span>
                        </td>
                        <td>
                          <p className="qa-review__misconceptions">{misconceptionText(item)}</p>
                        </td>
                        <td>
                          <ul className="qa-review__options">
                            {optionEntries(item.options).map(([label, text]) => (
                              <li
                                key={label}
                                className={
                                  label === item.correct_label
                                    ? "qa-review__option--correct"
                                    : undefined
                                }
                              >
                                <span className="qa-review__key">{label}.</span>{" "}
                                <MarkdownContent inline>{presentQuizText(text)}</MarkdownContent>
                              </li>
                            ))}
                          </ul>
                        </td>
                        <td>
                          <strong>{item.correct_label}</strong>
                        </td>
                        <td>
                          <p className="qa-review__rationale">{item.correct_rationale}</p>
                          {optionEntries(item.distractor_rationales).map(([label, text]) => (
                            <p key={label} className="qa-review__rationale muted">
                              {label}: {text}
                            </p>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
