import { useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { askQuestion, type Confidence } from "../../api/textToSql";
import { Crumbs } from "../../components/Crumbs";
import { MarkdownContent } from "../../components/MarkdownContent";
import { PushButton } from "../../components/PushButton";

type ThreadEntry = {
  id: string;
  question: string;
  status: "pending" | "done" | "error";
  answer?: string;
  confidence?: Confidence | null;
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
    questions: ["Who took the last quiz?"],
  },
];

const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: "High confidence",
  medium: "Preliminary",
  low: "Low confidence",
};

function ConfidenceBadge({ confidence }: { confidence: Confidence | null | undefined }) {
  if (!confidence) return null;
  const modifier = confidence === "high" ? "badge--ok" : "badge--warn";
  return <span className={`badge ${modifier}`}>{CONFIDENCE_LABEL[confidence]}</span>;
}

/**
 * Teacher-only ask-the-data chat. Route protection already lives one layer up — the whole
 * `/teacher/*` subtree is wrapped in `RequireRole roles={ROLE_TEACHER}` (see App.tsx), so
 * this component is never rendered, and the "Assistant" nav link in TeacherShell is never
 * shown, for anyone who isn't a teacher. There is no second, component-level role check
 * here on purpose — one real gate, not two places that could quietly disagree.
 */
export function AssistantPage() {
  const [thread, setThread] = useState<ThreadEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  function onPickSuggestion(event: ChangeEvent<HTMLSelectElement>) {
    const question = event.target.value;
    if (!question) return;
    // Fill the box rather than sending: the teacher can adjust the wording (e.g. a different
    // attendance threshold) and presses Ask when ready. The select is controlled with
    // value="" below, so it returns to its placeholder after every pick.
    setDraft(question);
    inputRef.current?.focus();
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;

    const id = `q-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setDraft("");
    setBusy(true);
    setThread((prev) => [...prev, { id, question: text, status: "pending" }]);

    try {
      // Single-turn: only the current question is sent. No prior thread entries are read
      // here or passed to askQuestion — the thread below is a display concern only.
      const result = await askQuestion(text);
      setThread((prev) =>
        prev.map((entry) =>
          entry.id === id
            ? {
                ...entry,
                status: "done",
                answer: result.natural_answer,
                confidence: result.confidence,
              }
            : entry,
        ),
      );
    } catch {
      // A real HTTP-layer failure (403/500/504/network) — distinct from a normal 200
      // response that happens to carry a low-confidence or refusal answer, which never
      // reaches this branch at all since it doesn't throw.
      setThread((prev) =>
        prev.map((entry) => (entry.id === id ? { ...entry, status: "error" } : entry)),
      );
    } finally {
      setBusy(false);
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }

  return (
    <div className="assistant-chat">
      <h1 className="sr-only">Assistant</h1>
      <Crumbs parts={[{ label: "My classes", to: "/teacher" }, { label: "Assistant" }]} />

      <div className="assistant-chat__thread" aria-live="polite">
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
              <p className="assistant-chat__typing muted" role="status">
                Thinking…
              </p>
            )}
            {entry.status === "done" && (
              <div
                className="assistant-chat__bubble assistant-chat__bubble--assistant"
                aria-label="Assistant"
              >
                <MarkdownContent className="assistant-chat__body markdown">
                  {entry.answer ?? ""}
                </MarkdownContent>
                <ConfidenceBadge confidence={entry.confidence} />
              </div>
            )}
            {entry.status === "error" && (
              <p className="banner banner--warning" role="alert">
                {GENERIC_ERROR_MESSAGE}
              </p>
            )}
          </article>
        ))}
        <div ref={bottomRef} />
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
          placeholder="Or type your own question, e.g. How many students do I teach in total?"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={busy}
        />
        <PushButton type="submit" disabled={busy || !draft.trim()} loading={busy}>
          Ask
        </PushButton>
      </form>
    </div>
  );
}
