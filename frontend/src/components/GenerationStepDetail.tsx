import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { getGenerationStepDetail } from "../api/generation";
import {
  ApiError,
  type GenerationChunkPreview,
  type GenerationStepDetail,
  type GenerationStepId,
  type GenerationStepStatus,
  type JudgeReadout,
  type LessonMaterial,
} from "../api/types";
import { slidesForLessonView } from "../lib/lessonSlides";
import { LessonSlideFrame } from "./LessonSlideFrame";
import { QuizItemCards } from "./QuizItemCards";
import "./GenerationStepDetail.css";

type GenerationStepDetailProps = {
  runId: string;
  step: GenerationStepId;
  publishedHref?: string | null;
  onClose: () => void;
};

const STEP_LABELS: Record<GenerationStepId, string> = {
  indexing: "Indexing PDF",
  outlining: "Determining outline",
  outline_review: "Outline review",
  lesson: "Generating lesson",
  items: "Generating quiz items",
  ready: "Ready to review",
};

function statusWord(status: GenerationStepStatus): string {
  switch (status) {
    case "complete":
      return "Complete";
    case "current":
      return "Current";
    case "pending":
      return "Not run yet";
    case "failed":
      return "Failed";
    default: {
      const _never: never = status;
      return _never;
    }
  }
}

function formatWhen(iso: string | null): string {
  if (!iso) return "Not recorded";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString();
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "Not recorded";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes <= 0) return `${rest}s`;
  return `${minutes}m ${rest}s`;
}

function humanToken(value: string | null): string {
  if (!value) return "Not recorded";
  return value.replaceAll("_", " ");
}

function draftLessonMaterial(markdown: string): LessonMaterial {
  return {
    id: "step-lesson",
    title: "Generated lesson",
    markdown,
    slides: [],
    source_material_version_id: "",
    progress: null,
    quiz_unlocked: false,
    quiz_id: null,
  };
}

function ChunkList({ chunks }: { chunks: GenerationChunkPreview[] }) {
  if (chunks.length === 0) return <p className="muted">No chunks.</p>;
  return (
    <ul className="step-detail__chunks">
      {chunks.map((chunk) => (
        <li key={`${chunk.ordinal}-${chunk.page_number ?? "page"}`}>
          <p className="step-detail__chunk-meta">
            Chunk {chunk.ordinal}
            {chunk.page_number != null ? ` · page ${chunk.page_number}` : ""}
            {chunk.section_heading ? ` · ${chunk.section_heading}` : ""}
          </p>
          <p className="step-detail__chunk-preview">{chunk.preview}</p>
        </li>
      ))}
    </ul>
  );
}

function OutlineTree({
  nodes,
  parentId,
}: {
  nodes: NonNullable<GenerationStepDetail["outline"]>["nodes"];
  parentId: string | null;
}) {
  const rows = nodes
    .filter((node) => node.parent_id === parentId)
    .sort((left, right) => left.sequence - right.sequence);
  if (rows.length === 0) return null;
  return (
    <ul className="step-detail__outline">
      {rows.map((node) => (
        <li key={node.id}>
          <h4>{node.title}</h4>
          {node.quota != null ? <p className="muted">Quota {node.quota}</p> : null}
          {node.proposed_outcomes.length > 0 ? (
            <ul>
              {node.proposed_outcomes.map((outcome) => (
                <li key={outcome}>{outcome}</li>
              ))}
            </ul>
          ) : null}
          {node.chunks.length > 0 ? <ChunkList chunks={node.chunks} /> : null}
          <OutlineTree nodes={nodes} parentId={node.id} />
        </li>
      ))}
    </ul>
  );
}

function decisionWord(decision: string): string {
  switch (decision) {
    case "accept":
      return "Accept";
    case "needs_review":
      return "Needs review";
    case "reject":
      return "Reject";
    case "error":
      return "Error";
    default:
      return decision;
  }
}

function probabilityLine(probabilities: Record<string, number>): string {
  return Object.entries(probabilities)
    .map(([label, value]) => `${label} ${value.toFixed(2)}`)
    .join(" · ");
}

function JudgeScores({ readouts }: { readouts: JudgeReadout[] }) {
  if (readouts.length === 0) return null;
  return (
    <div className="step-detail__judge">
      <h4>Jev scores</h4>
      <ul className="step-detail__judge-list">
        {readouts.map((readout) => (
          <li key={`${readout.subject_kind}-${readout.subject_key ?? readout.label}`}>
            <p>
              <span className={`step-detail__judge-decision is-${readout.decision}`}>
                {decisionWord(readout.decision)}
              </span>{" "}
              {readout.label}
              {readout.model ? <span className="muted"> · {readout.model}</span> : null}
            </p>
            <ul>
              {readout.checks.map((check) => (
                <li key={check.check_id}>
                  {check.check_id}: {check.summary} · {decisionWord(check.decision)}
                  {probabilityLine(check.probabilities)
                    ? ` · ${probabilityLine(check.probabilities)}`
                    : ""}
                </li>
              ))}
            </ul>
            {readout.reasons.map((reason) => (
              <p key={reason} className="muted">
                {reason}
              </p>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

function mixLine(mix: Record<string, number>): string {
  return Object.entries(mix)
    .filter(([, count]) => count > 0)
    .map(([label, count]) => `${label} ${count}`)
    .join(" · ");
}

export function GenerationStepDetailPanel({
  runId,
  step,
  publishedHref = null,
  onClose,
}: GenerationStepDetailProps) {
  const headingId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [detail, setDetail] = useState<GenerationStepDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const abort = new AbortController();
    setDetail(null);
    setError(null);
    setQuery("");
    void getGenerationStepDetail(runId, step)
      .then((next) => {
        if (!abort.signal.aborted && next) setDetail(next);
      })
      .catch((err: unknown) => {
        if (abort.signal.aborted) return;
        setError(err instanceof ApiError ? err.message : "Could not load this step.");
      });
    return () => abort.abort();
  }, [runId, step]);

  useEffect(() => {
    headingRef.current?.focus();
  }, [step, detail]);

  const slides = useMemo(() => {
    const markdown = detail?.lesson?.markdown;
    if (!markdown) return [];
    return slidesForLessonView(draftLessonMaterial(markdown));
  }, [detail?.lesson?.markdown]);

  const chunks = detail?.indexing?.chunks ?? [];
  const filteredChunks = chunks.filter((chunk) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    const haystack = `${chunk.preview} ${chunk.section_heading ?? ""} ${chunk.page_number ?? ""}`;
    return haystack.toLowerCase().includes(needle);
  });

  const timing = detail?.timing;
  const quizHref = publishedHref ? `${publishedHref}#published-quiz` : null;

  return (
    <section
      className="step-detail"
      role="region"
      aria-labelledby={headingId}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="step-detail__bar">
        <h3 id={headingId} tabIndex={-1} ref={headingRef}>
          {STEP_LABELS[step]}
        </h3>
        <button type="button" className="step-detail__close" onClick={onClose}>
          Close
        </button>
      </div>
      {error ? (
        <p className="form__error" role="alert">
          {error}
        </p>
      ) : null}
      {!detail && !error ? (
        <p className="muted" role="status">
          Loading this step…
        </p>
      ) : null}
      {timing ? (
        <dl className="step-detail__timing">
          <div>
            <dt>Status</dt>
            <dd>{statusWord(timing.status)}</dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd>{formatWhen(timing.started_at)}</dd>
          </div>
          <div>
            <dt>Ended</dt>
            <dd>{formatWhen(timing.ended_at)}</dd>
          </div>
          <div>
            <dt>Duration</dt>
            <dd>{formatDuration(timing.duration_seconds)}</dd>
          </div>
        </dl>
      ) : null}
      {timing?.error ? (
        <p className="form__error" role="alert">
          {timing.error}
        </p>
      ) : null}
      {timing && !timing.available ? (
        <p className="muted">{timing.hint ?? "This step has not run yet."}</p>
      ) : null}
      {detail?.indexing ? (
        <div className="step-detail__body">
          <p>
            {detail.indexing.page_count == null
              ? "Page count not recorded"
              : `${detail.indexing.page_count} pages`}
            {" · "}
            {detail.indexing.chunk_count === 1
              ? "1 chunk"
              : `${detail.indexing.chunk_count} chunks`}
            {detail.indexing.truncated ? " · list truncated" : ""}
          </p>
          <label className="form__label" htmlFor={`${headingId}-search`}>
            Search chunks
          </label>
          <input
            id={`${headingId}-search`}
            className="form__input"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search chunk text or page"
          />
          <ChunkList chunks={filteredChunks} />
        </div>
      ) : null}
      {detail?.outline ? (
        <div className="step-detail__body">
          <p className="muted">Target item count {detail.outline.target_item_count}</p>
          <OutlineTree nodes={detail.outline.nodes} parentId={null} />
          {detail.outline.unmapped_chunks.length > 0 ? (
            <div>
              <h4>Unmapped chunks</h4>
              <ChunkList chunks={detail.outline.unmapped_chunks} />
            </div>
          ) : null}
        </div>
      ) : null}
      {detail?.outline_review ? (
        <div className="step-detail__body">
          {detail.outline_review.rounds.length === 0 ? (
            <p className="muted">No outline review rounds were recorded.</p>
          ) : (
            detail.outline_review.rounds.map((round) => (
              <article key={round.id} className="step-detail__round">
                <h4>
                  Round {round.number} · {humanToken(round.stage)}
                </h4>
                <p className="muted">
                  Opened {formatWhen(round.opened_at)}
                  {round.sealed_at ? ` · sealed ${formatWhen(round.sealed_at)}` : " · open"}
                </p>
                {round.decisions.length === 0 ? (
                  <p className="muted">No decisions recorded.</p>
                ) : (
                  <ul>
                    {round.decisions.map((decision) => (
                      <li key={`${decision.reviewer}-${decision.verdict}`}>
                        {decision.reviewer}: {humanToken(decision.verdict)}
                      </li>
                    ))}
                  </ul>
                )}
                {round.comments.map((comment) => (
                  <p key={`${comment.author}-${comment.comment}`}>
                    <strong>{comment.author}</strong>
                    {comment.target_title ? ` on ${comment.target_title}` : ""}: {comment.comment}
                  </p>
                ))}
                <p>
                  {round.closure_action
                    ? `Closed by ${round.closed_by ?? "unknown"} · ${humanToken(round.closure_action)}`
                    : "Not closed yet."}
                </p>
                {round.rationale ? <p className="muted">{round.rationale}</p> : null}
              </article>
            ))
          )}
        </div>
      ) : null}
      {detail?.lesson ? (
        <div className="step-detail__body">
          {detail.lesson.needs_review ? (
            <p className="qa-review__disputed">
              <span className="badge badge--warn">Needs review before publish</span>
            </p>
          ) : null}
          <JudgeScores readouts={detail.lesson.judge ?? []} />
          {slides.length === 0 ? (
            <p className="muted">No lesson markdown was stored for this step.</p>
          ) : (
            <div className="step-detail__slides">
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
          {publishedHref && detail.lesson.published_lesson_version_id ? (
            <p>
              <Link to={publishedHref}>Open published lesson</Link>
            </p>
          ) : null}
        </div>
      ) : null}
      {detail?.items ? (
        <div className="step-detail__body">
          <JudgeScores readouts={detail.items.judge ?? []} />
          <p>
            {detail.items.count === 1 ? "1 item" : `${detail.items.count} items`}
            {mixLine(detail.items.difficulty_mix)
              ? ` · difficulty ${mixLine(detail.items.difficulty_mix)}`
              : ""}
          </p>
          {mixLine(detail.items.bloom_mix) ? (
            <p className="muted">Bloom {mixLine(detail.items.bloom_mix)}</p>
          ) : null}
          {detail.items.subtopic_counts.length > 0 ? (
            <ul>
              {detail.items.subtopic_counts.map((row) => (
                <li key={row.subtopic_id}>
                  {row.title}: {row.count}
                </li>
              ))}
            </ul>
          ) : null}
          <p>
            {detail.items.passed ?? 0} accepted · {detail.items.fixed_on_retry ?? 0} retried ·{" "}
            {detail.items.dropped ?? 0} rejected
            {detail.items.key_disputed > 0
              ? ` · ${detail.items.key_disputed} key disputed`
              : ""}
          </p>
          {detail.items.items.length > 0 ? (
            <QuizItemCards items={detail.items.items} />
          ) : (
            <p className="muted">No quiz items were stored for this step.</p>
          )}
        </div>
      ) : null}
      {detail?.ready ? (
        <div className="step-detail__body">
          <p>
            {detail.ready.published ? "Published" : "Not published"} · {detail.ready.phase}
          </p>
          <dl className="step-detail__timing">
            <div>
              <dt>Created</dt>
              <dd>{formatWhen(detail.ready.created_at)}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatWhen(detail.ready.updated_at)}</dd>
            </div>
            <div>
              <dt>Published at</dt>
              <dd>{formatWhen(detail.ready.published_at)}</dd>
            </div>
          </dl>
          {detail.ready.lesson_version_id ? (
            <p className="muted">Lesson version {detail.ready.lesson_version_id}</p>
          ) : null}
          {detail.ready.quiz_version_id ? (
            <p className="muted">Quiz version {detail.ready.quiz_version_id}</p>
          ) : null}
          {publishedHref && detail.ready.published ? (
            <p className="step-detail__links">
              <Link to={publishedHref}>Open published lesson</Link>
              {quizHref ? <Link to={quizHref}>Open published quiz</Link> : null}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
