import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { ApiError } from "../../api/types";
import { ProgressReconnectError } from "../../api/progress";
import {
  askQuestion,
  askQuestionStream,
  TEXT_TO_SQL_STAGES,
  TEXT_TO_SQL_STAGE_LABEL,
  type Confidence,
  type TextToSqlProgress,
  type TextToSqlStage,
  type TextToSqlTable,
} from "../../api/textToSql";
import { Crumbs } from "../../components/Crumbs";
import { claimHaptic, triggerHaptic } from "../../lib/haptics";
import { MarkdownContent } from "../../components/MarkdownContent";
import { ProgressReconnectStatus, ProgressStreamAlert } from "../../components/ProgressReconnect";
import { PushButton } from "../../components/PushButton";

type StageState = {
  status: TextToSqlProgress["status"];
  message: string | null;
  percent: number | null;
};

type EntryStatus = "pending" | "done" | "refusal" | "error" | "cancelled";

type ThreadEntry = {
  id: string;
  question: string;
  status: EntryStatus;
  stages: Partial<Record<TextToSqlStage, StageState>>;
  answer: string;
  table?: TextToSqlTable;
  confidence?: Confidence | null;
  provenance?: string | null;
  refusal?: string;
  error?: string;
  reconnecting: boolean;
};

const GENERIC_ERROR_MESSAGE = "Something went wrong — please try again.";

/**
 * Questions offered in the "Suggested questions" dropdown. Every one has been checked against
 * the database for several teachers and answers correctly for any teacher, so none names a
 * subject or section (a teacher who does not teach Science must not be offered a Science
 * question). The wording is exact on purpose: the question router is sensitive to phrasing,
 * so only these tested forms are used. The free-text box below stays available for anything else.
 */
const SUGGESTED_QUESTIONS: { group: string; questions: string[] }[] = [
  {
    group: "Class overview",
    questions: [
      "How many students do I teach in total?",
      "Which subjects am I assigned to?",
      "What sections am I assigned to?",
    ],
  },
  {
    group: "Performance and attendance",
    questions: [
      "What is the average attendance rate for my students this term?",
      "Show me the struggling students.",
      "How many of my students are doing well?",
      "How many students have attendance less than 85%?",
    ],
  },
  {
    group: "Quizzes",
    questions: [
      "Who took the last quiz?",
      "What is the average quiz score for my students?",
      "What was the highest quiz score?",
    ],
  },
];

const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: "High confidence",
  medium: "Preliminary",
  low: "Low confidence",
};

function scrollThreadToEnd(node: HTMLElement | null) {
  if (!node) return;
  try {
    node.scrollTo({ top: node.scrollHeight });
  } catch {
    node.scrollTop = node.scrollHeight;
  }
}

function ConfidenceBadge({ confidence }: { confidence: Confidence | null | undefined }) {
  if (!confidence) return null;
  const modifier = confidence === "high" ? "badge--ok" : "badge--warn";
  return <span className={`badge ${modifier}`}>{CONFIDENCE_LABEL[confidence]}</span>;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function isStreamMissing(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 405);
}

type StageVisual = "pending" | "current" | "complete" | "failed" | "skipped";

function stageVisual(
  stage: TextToSqlStage,
  stages: Partial<Record<TextToSqlStage, StageState>>,
): StageVisual {
  const known = stages[stage];
  if (known?.status === "failed") return "failed";
  if (known?.status === "done") return "complete";
  if (known?.status === "running") return "current";
  const index = TEXT_TO_SQL_STAGES.indexOf(stage);
  const laterStarted = TEXT_TO_SQL_STAGES.slice(index + 1).some((later) => stages[later] !== undefined);
  if (laterStarted) return "skipped";
  return "pending";
}

function stageVisualLabel(visual: StageVisual): string {
  switch (visual) {
    case "pending":
      return "Pending";
    case "current":
      return "In progress";
    case "complete":
      return "Done";
    case "failed":
      return "Failed";
    case "skipped":
      return "Not used";
    default: {
      const _never: never = visual;
      return _never;
    }
  }
}

function latestRunningStage(
  stages: Partial<Record<TextToSqlStage, StageState>>,
): TextToSqlStage | undefined {
  for (let index = TEXT_TO_SQL_STAGES.length - 1; index >= 0; index -= 1) {
    const stage = TEXT_TO_SQL_STAGES[index];
    if (stage !== undefined && stages[stage]?.status === "running") return stage;
  }
  return undefined;
}

function currentStageMessage(stages: Partial<Record<TextToSqlStage, StageState>>): {
  message: string;
  percent: number | null;
} | null {
  for (let index = TEXT_TO_SQL_STAGES.length - 1; index >= 0; index -= 1) {
    const stage = TEXT_TO_SQL_STAGES[index];
    if (stage === undefined) continue;
    const known = stages[stage];
    if (known?.status === "running" && known.message) {
      return { message: known.message, percent: known.percent };
    }
  }
  return null;
}

function cellText(value: TextToSqlTable["rows"][number][number]): string {
  if (value === null) return "";
  return String(value);
}

function AskStageStepper({ stages }: { stages: Partial<Record<TextToSqlStage, StageState>> }) {
  const live = currentStageMessage(stages);
  return (
    <div className="assistant-chat__stages">
      <ol className="generation-stepper" aria-label="Answer progress">
        {TEXT_TO_SQL_STAGES.map((stage) => {
          const visual = stageVisual(stage, stages);
          return (
            <li
              key={stage}
              className={`generation-stepper__step is-${visual}`}
              aria-current={stage === latestRunningStage(stages) ? "step" : undefined}
            >
              <span className="generation-stepper__label">{TEXT_TO_SQL_STAGE_LABEL[stage]}</span>
              <span className="generation-stepper__state">{stageVisualLabel(visual)}</span>
            </li>
          );
        })}
      </ol>
      <p className="assistant-chat__stage-message" role="status" aria-live="polite" aria-atomic="true">
        {live ? (
          <>
            {live.message}
            {live.percent !== null ? (
              <span className="assistant-chat__percent"> {live.percent}%</span>
            ) : null}
          </>
        ) : (
          <span className="sr-only">Waiting for the next update</span>
        )}
      </p>
      {live?.percent !== null && live ? (
        <progress className="assistant-chat__meter" max={100} value={live.percent}>
          {live.percent}%
        </progress>
      ) : null}
    </div>
  );
}

function ResultTable({ table }: { table: TextToSqlTable }) {
  return (
    <div className="table-scroll assistant-chat__table-wrap">
      <table className="data-table data-table--cards">
        <caption className="sr-only">Query results</caption>
        <thead>
          <tr>
            {table.columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => {
                const label = table.columns[cellIndex] ?? "";
                return (
                  <td key={`${label}-${cellIndex}`} data-label={label}>
                    {cellText(cell)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Teacher-only ask-the-data chat. Route protection already lives one layer up — the whole
 * `/teacher/*` subtree is wrapped in `RequireRole roles={ROLE_TEACHER}` (see App.tsx), so
 * this component is never rendered, and the "Assistant" nav link in TeacherShell is never
 * shown, for anyone who isn't a teacher. There is no second, component-level role check
 * here on purpose — one real gate, not two places that could quietly disagree.
 */
function emptyEntry(id: string, question: string): ThreadEntry {
  return {
    id,
    question,
    status: "pending",
    stages: {},
    answer: "",
    reconnecting: false,
  };
}

export function AssistantPage() {
  const [thread, setThread] = useState<ThreadEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const runAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    scrollThreadToEnd(threadRef.current);
  }, [thread, busy]);

  useEffect(() => {
    return () => {
      runAbort.current?.abort();
    };
  }, []);

  function patchEntry(id: string, update: (entry: ThreadEntry) => ThreadEntry) {
    setThread((prev) => prev.map((entry) => (entry.id === id ? update(entry) : entry)));
  }

  function onPickSuggestion(event: ChangeEvent<HTMLSelectElement>) {
    const question = event.target.value;
    if (!question) return;
    // Fill the box rather than sending: the teacher can adjust the wording (e.g. a different
    // attendance threshold) and presses Ask when ready. The select is controlled with
    // value="" below, so it returns to its placeholder after every pick.
    setDraft(question);
    inputRef.current?.focus();
  }

  async function runQuestion(id: string, text: string) {
    runAbort.current?.abort();
    const controller = new AbortController();
    runAbort.current = controller;
    setBusy(true);
    patchEntry(id, (entry) => ({
      ...emptyEntry(id, entry.question),
      question: text,
    }));

    try {
      // Single-turn: only the current question is sent. No prior thread entries are read
      // here or passed along — the thread below is a display concern only.
      const result = await askQuestionStream(text, {
        signal: controller.signal,
        onProgress: (progress) => {
          patchEntry(id, (entry) => ({
            ...entry,
            stages: {
              ...entry.stages,
              [progress.stage]: {
                status: progress.status,
                message: progress.message,
                percent: progress.percent,
              },
            },
          }));
        },
        onAnswerReset: () => {
          patchEntry(id, (entry) => ({
            ...entry,
            answer: "",
            table: undefined,
            stages: {},
          }));
        },
        onAnswerDelta: (delta) => {
          patchEntry(id, (entry) => ({ ...entry, answer: entry.answer + delta }));
        },
        onTable: (table) => {
          patchEntry(id, (entry) => ({ ...entry, table }));
        },
        onReconnecting: () => {
          patchEntry(id, (entry) => ({ ...entry, reconnecting: true }));
        },
        onReconnected: () => {
          patchEntry(id, (entry) => ({ ...entry, reconnecting: false }));
        },
      });
      if (controller.signal.aborted) return;
      switch (result.kind) {
        case "final":
          patchEntry(id, (entry) => ({
            ...entry,
            status: "done",
            answer: result.answer.natural_answer,
            confidence: result.answer.confidence,
            provenance: result.answer.provenance,
            reconnecting: false,
          }));
          triggerHaptic("success");
          break;
        case "refusal":
          patchEntry(id, (entry) => ({
            ...entry,
            status: "refusal",
            refusal: result.message,
            reconnecting: false,
          }));
          triggerHaptic("success");
          break;
        case "error":
          patchEntry(id, (entry) => ({
            ...entry,
            status: "error",
            error: result.message,
            reconnecting: false,
          }));
          triggerHaptic("error");
          break;
        default: {
          const _never: never = result;
          return _never;
        }
      }
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) return;
      if (isStreamMissing(error)) {
        try {
          const fallback = await askQuestion(text);
          if (controller.signal.aborted) return;
          patchEntry(id, (entry) => ({
            ...entry,
            status: "done",
            answer: fallback.natural_answer,
            confidence: fallback.confidence,
            provenance: fallback.provenance,
            stages: {},
            reconnecting: false,
          }));
          triggerHaptic("success");
        } catch {
          if (controller.signal.aborted) return;
          triggerHaptic("error");
          patchEntry(id, (entry) => ({
            ...entry,
            status: "error",
            error: GENERIC_ERROR_MESSAGE,
            reconnecting: false,
          }));
        }
        return;
      }
      triggerHaptic("error");
      const message =
        error instanceof ProgressReconnectError ? error.message : GENERIC_ERROR_MESSAGE;
      patchEntry(id, (entry) => ({
        ...entry,
        status: "error",
        error: message,
        reconnecting: false,
      }));
    } finally {
      if (runAbort.current === controller) {
        setBusy(false);
      }
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    claimHaptic();
    const id = `q-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setDraft("");
    setThread((prev) => [...prev, emptyEntry(id, text)]);
    void runQuestion(id, text);
  }

  function onCancel() {
    runAbort.current?.abort();
    setBusy(false);
    setThread((prev) =>
      prev.map((entry) =>
        entry.status === "pending" ? { ...entry, status: "cancelled", reconnecting: false } : entry,
      ),
    );
  }

  function onRetry(entry: ThreadEntry) {
    if (busy) return;
    claimHaptic();
    void runQuestion(entry.id, entry.question);
  }

  return (
    <div className="assistant-chat">
      <h1 className="sr-only">Assistant</h1>
      <Crumbs parts={[{ label: "My classes", to: "/teacher" }, { label: "Assistant" }]} />

      <div className="assistant-chat__thread" ref={threadRef}>
        {thread.length === 0 && (
          <p className="assistant-chat__empty muted">
            Ask about your classes. Pick a suggested question below, or type your own — e.g.
            "How many students do I teach in total?" or "Show me the struggling students."
          </p>
        )}
        {thread.map((entry) => (
          <article className="assistant-chat__exchange" key={entry.id}>
            <div className="assistant-chat__bubble assistant-chat__bubble--user" aria-label="You">
              <p className="assistant-chat__body">{entry.question}</p>
            </div>
            {entry.status === "pending" && (
              <div className="assistant-chat__live" aria-label="Assistant">
                <ProgressReconnectStatus active={entry.reconnecting} />
                <AskStageStepper stages={entry.stages} />
                {entry.answer ? (
                  <p className="assistant-chat__body" aria-live="polite">
                    {entry.answer}
                  </p>
                ) : null}
                {entry.table ? <ResultTable table={entry.table} /> : null}
                <div className="assistant-chat__actions">
                  <PushButton type="button" size="sm" onClick={onCancel}>
                    Cancel
                  </PushButton>
                </div>
              </div>
            )}
            {entry.status === "done" && (
              <div
                className="assistant-chat__bubble assistant-chat__bubble--assistant"
                aria-label="Assistant"
              >
                {Object.keys(entry.stages).length > 0 ? <AskStageStepper stages={entry.stages} /> : null}
                {entry.table ? <ResultTable table={entry.table} /> : null}
                <div aria-live="polite">
                  <MarkdownContent className="assistant-chat__body markdown">
                    {entry.answer}
                  </MarkdownContent>
                </div>
                <ConfidenceBadge confidence={entry.confidence} />
                {entry.provenance ? (
                  <p className="assistant-chat__provenance">{entry.provenance}</p>
                ) : null}
              </div>
            )}
            {entry.status === "refusal" && (
              <div
                className="assistant-chat__bubble assistant-chat__bubble--assistant"
                aria-label="Assistant"
              >
                {Object.keys(entry.stages).length > 0 ? <AskStageStepper stages={entry.stages} /> : null}
                <p className="assistant-chat__body" role="status">
                  {entry.refusal}
                </p>
              </div>
            )}
            {entry.status === "error" && (
              <ProgressStreamAlert
                message={entry.error ?? GENERIC_ERROR_MESSAGE}
                busy={busy}
                onRetry={() => onRetry(entry)}
              />
            )}
            {entry.status === "cancelled" && (
              <p className="assistant-chat__typing muted">Cancelled.</p>
            )}
          </article>
        ))}
      </div>

      <div className="assistant-chat__suggest">
        <label className="assistant-chat__suggest-label" htmlFor="assistant-chat-suggestions">
          Suggested questions
        </label>
        <select
          id="assistant-chat-suggestions"
          className="assistant-chat__suggest-select"
          value=""
          onChange={onPickSuggestion}
          disabled={busy}
        >
          <option value="" disabled>
            Choose a question…
          </option>
          {SUGGESTED_QUESTIONS.map(({ group, questions }) => (
            <optgroup key={group} label={group}>
              {questions.map((question) => (
                <option key={question} value={question}>
                  {question}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>

      <form className="assistant-chat__composer" onSubmit={(e) => void onSubmit(e)}>
        <label className="sr-only" htmlFor="assistant-chat-input">
          Question
        </label>
        <textarea
          id="assistant-chat-input"
          ref={inputRef}
          className="assistant-chat__input"
          rows={2}
          maxLength={2000}
          placeholder="Or type your own question, e.g. How many students do I teach in total?"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={busy}
        />
        <PushButton
          type="submit"
          disabled={busy || !draft.trim()}
          loading={busy}
          onClick={() => {
            if (!busy && draft.trim()) claimHaptic();
          }}
        >
          Ask
        </PushButton>
      </form>
    </div>
  );
}
