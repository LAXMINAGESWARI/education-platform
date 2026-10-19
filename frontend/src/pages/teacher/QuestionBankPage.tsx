import { useEffect, useMemo, useState } from "react";

import { Crumbs } from "../../components/Crumbs";
import { MarkdownContent } from "../../components/MarkdownContent";
import { PushButton } from "../../components/PushButton";
import { presentQuizText } from "../../lib/quizText";
import {
  discardDraft,
  fetchApproved,
  fetchAuthorableSubtopics,
  fetchDrafts,
  generateQuestions,
  publishDraft,
  type AuthorableSubtopic,
  type Difficulty,
  type DraftQuestion,
} from "../../api/authoring";
import { csvFilename, downloadCsv, questionsToCsv } from "../../lib/questionCsv";

const DIFFICULTIES: Difficulty[] = ["easy", "medium", "hard"];

type Tab = "drafts" | "approved";

/** "(2 waiting, 7 approved)" — both, so approving something never looks like losing it. */
function countSuffix(subtopic: AuthorableSubtopic): string {
  const parts = [
    subtopic.draft_count > 0 ? `${subtopic.draft_count} waiting` : "",
    subtopic.published_count > 0 ? `${subtopic.published_count} approved` : "",
  ].filter(Boolean);
  return parts.length ? ` (${parts.join(", ")})` : "";
}

export function QuestionBankPage() {
  const [subtopics, setSubtopics] = useState<AuthorableSubtopic[]>([]);
  const [selected, setSelected] = useState("");
  const [count, setCount] = useState(5);
  const [difficulty, setDifficulty] = useState<Difficulty>("medium");

  const [tab, setTab] = useState<Tab>("drafts");
  const [drafts, setDrafts] = useState<DraftQuestion[]>([]);
  const [approved, setApproved] = useState<DraftQuestion[]>([]);
  const [rejected, setRejected] = useState<string[]>([]);
  const [generating, setGenerating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchAuthorableSubtopics()
      .then((list) => {
        if (cancelled) return;
        setSubtopics(list);
        setSelected((current) => current || list[0]?.id || "");
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Could not load your subjects."),
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setRejected([]);
    // Both lists, always: approving a draft moves a question from one to the other, and
    // the counts have to agree whichever tab is open when it happens.
    void Promise.all([fetchDrafts(selected), fetchApproved(selected)])
      .then(([draftList, approvedList]) => {
        if (cancelled) return;
        setDrafts(draftList);
        setApproved(approvedList);
      })
      .catch(() => {
        if (cancelled) return;
        setDrafts([]);
        setApproved([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const grouped = useMemo(() => {
    const bySubject = new Map<string, AuthorableSubtopic[]>();
    for (const subtopic of subtopics) {
      const list = bySubject.get(subtopic.subject) ?? [];
      list.push(subtopic);
      bySubject.set(subtopic.subject, list);
    }
    return [...bySubject.entries()];
  }, [subtopics]);

  const current = subtopics.find((s) => s.id === selected);
  const visible = tab === "drafts" ? drafts : approved;

  /** Re-read the per-subtopic counts in the picker, which go stale as soon as you decide. */
  async function refreshCounts() {
    try {
      setSubtopics(await fetchAuthorableSubtopics());
    } catch {
      // A stale count in the dropdown is not worth an error message; the tabs are live.
    }
  }

  async function onGenerate() {
    if (!selected || generating) return;
    setGenerating(true);
    setError(null);
    setNote(null);
    try {
      const result = await generateQuestions(selected, count, difficulty);
      setDrafts(result.drafts);
      setRejected(result.rejected);
      // New drafts are the thing to look at, so show them even if the bank tab was open.
      setTab("drafts");
      void refreshCounts();
      setNote(
        result.created === 0
          ? "No usable questions came back. Try again, or a different difficulty."
          : `${result.created} new draft${result.created === 1 ? "" : "s"} added.`,
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Generation failed.");
    } finally {
      setGenerating(false);
    }
  }

  async function decide(draft: DraftQuestion, keep: boolean) {
    setBusyId(draft.id);
    setError(null);
    try {
      if (keep) {
        await publishDraft(draft.id);
      } else {
        await discardDraft(draft.id);
      }
      setDrafts((list) => list.filter((d) => d.id !== draft.id));
      if (keep) {
        // Move it across rather than refetching: the teacher should see where it went.
        setApproved((list) => [...list, draft]);
      }
      setNote(
        keep
          ? "Question approved. It is in the Approved tab now."
          : "Draft discarded. It is archived, not deleted.",
      );
      void refreshCounts();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusyId(null);
    }
  }

  function onDownload() {
    if (!current || approved.length === 0) return;
    const context = {
      subject: current.subject,
      topic: current.topic,
      subtopic: current.name,
    };
    downloadCsv(csvFilename(context), questionsToCsv(approved, context));
  }

  return (
    <>
      <Crumbs parts={[{ label: "My classes", to: "/teacher" }, { label: "Question bank" }]} />
      <header className="page-head">
        <p className="kicker">Teacher · authoring</p>
        <h1>Question bank</h1>
        <p>
          Draft multiple-choice questions from a subtopic's learning outcomes. Nothing here
          reaches a student until you approve it.
        </p>
      </header>

      {loading && <div className="banner banner--info">Loading your subjects…</div>}

      {!loading && subtopics.length === 0 && (
        <div className="banner banner--info" role="status">
          You have no teaching assignments, so there is nothing to write questions for.
        </div>
      )}

      {subtopics.length > 0 && (
        <section className="card" aria-label="Generate questions">
          <div className="field">
            <label className="field__label" htmlFor="subtopic">
              Subtopic
            </label>
            <select
              id="subtopic"
              className="field__input"
              value={selected}
              onChange={(event) => setSelected(event.target.value)}
            >
              {grouped.map(([subject, list]) => (
                <optgroup key={subject} label={subject}>
                  {list.map((subtopic) => (
                    <option key={subtopic.id} value={subtopic.id}>
                      {subtopic.topic} · {subtopic.name}
                      {countSuffix(subtopic)}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          <div className="authoring-controls">
            <div className="field">
              <label className="field__label" htmlFor="count">
                How many
              </label>
              <input
                id="count"
                className="field__input"
                type="number"
                min={1}
                max={10}
                value={count}
                onChange={(event) => setCount(Number(event.target.value))}
              />
            </div>
            <div className="field">
              <span className="field__label">Difficulty</span>
              <div className="meta-row" role="group" aria-label="Difficulty">
                {DIFFICULTIES.map((level) => (
                  <button
                    key={level}
                    type="button"
                    className={`badge ${difficulty === level ? "badge--info" : ""}`}
                    aria-pressed={difficulty === level}
                    onClick={() => setDifficulty(level)}
                  >
                    {level}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="meta-row">
            <PushButton onClick={() => void onGenerate()} disabled={generating || !selected}>
              {generating ? "Writing questions…" : "Generate drafts"}
            </PushButton>
          </div>
        </section>
      )}

      {error && (
        <div className="banner banner--warning" role="alert">
          {error}
        </div>
      )}
      {note && !error && (
        <div className="banner banner--info" role="status">
          {note}
        </div>
      )}

      {rejected.length > 0 && (
        <div className="banner banner--warning" role="status">
          <strong>{rejected.length} discarded before you saw them.</strong> The checker
          rejected these:
          <ul>
            {rejected.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {subtopics.length > 0 && (
        <section aria-label="Question bank">
          <div className="bank-tabs" role="tablist" aria-label="Question bank">
            <button
              type="button"
              role="tab"
              id="tab-drafts"
              aria-selected={tab === "drafts"}
              aria-controls="panel-questions"
              className={`bank-tab ${tab === "drafts" ? "is-active" : ""}`}
              onClick={() => setTab("drafts")}
            >
              Awaiting approval ({drafts.length})
            </button>
            <button
              type="button"
              role="tab"
              id="tab-approved"
              aria-selected={tab === "approved"}
              aria-controls="panel-questions"
              className={`bank-tab ${tab === "approved" ? "is-active" : ""}`}
              onClick={() => setTab("approved")}
            >
              Approved ({approved.length})
            </button>
          </div>

          <div
            id="panel-questions"
            role="tabpanel"
            aria-labelledby={tab === "drafts" ? "tab-drafts" : "tab-approved"}
          >
            <div className="topics-section__head">
              <h2>
                {tab === "drafts"
                  ? `${drafts.length} draft${drafts.length === 1 ? "" : "s"} awaiting your approval`
                  : `${approved.length} approved question${approved.length === 1 ? "" : "s"}`}
              </h2>
              <p>
                {current ? `${current.subject} · ${current.topic} · ${current.name}. ` : ""}
                {tab === "drafts"
                  ? "Approve to add to the bank, or discard."
                  : "These are ready to be put into a quiz."}
              </p>
              {tab === "approved" && approved.length > 0 && (
                <div className="meta-row">
                  <PushButton variant="outline" size="sm" onClick={onDownload}>
                    Download as CSV
                  </PushButton>
                  <span className="progress-label">
                    Includes the correct answer — a marking sheet, not a handout.
                  </span>
                </div>
              )}
            </div>

            {visible.length === 0 && (
              <div className="banner banner--info" role="status">
                {tab === "drafts"
                  ? "Nothing waiting. Generate some drafts above."
                  : "Nothing approved for this subtopic yet. Approved drafts appear here."}
              </div>
            )}

            <div className="draft-list">
              {visible.map((question, index) => (
                <article key={question.id} className="card draft-card">
                  <div className="meta-row">
                    <span className="badge">Q{index + 1}</span>
                    {question.difficulty && (
                      <span className="badge badge--info">{question.difficulty}</span>
                    )}
                    {tab === "approved" && <span className="badge badge--ok">approved</span>}
                  </div>
                  <h3>
                    <MarkdownContent inline>{presentQuizText(question.prompt)}</MarkdownContent>
                  </h3>

                  <ul className="draft-options">
                    {question.options.map((option) => {
                      const isCorrect = option.label === question.correct_label;
                      return (
                        <li
                          key={option.label}
                          className={`draft-option ${isCorrect ? "is-correct" : ""}`}
                        >
                          <span className="draft-option__label">{option.label}</span>
                          <span>
                            <MarkdownContent inline>{presentQuizText(option.text)}</MarkdownContent>
                          </span>
                          {isCorrect && <span className="badge badge--ok">correct</span>}
                        </li>
                      );
                    })}
                  </ul>

                  {question.explanation && (
                    <p className="progress-label">
                      Why:{" "}
                      <MarkdownContent inline>{presentQuizText(question.explanation)}</MarkdownContent>
                    </p>
                  )}

                  {tab === "drafts" && (
                    <div className="meta-row draft-card__actions">
                      <PushButton
                        size="sm"
                        onClick={() => void decide(question, true)}
                        disabled={busyId === question.id}
                      >
                        Approve
                      </PushButton>
                      <PushButton
                        variant="outline"
                        size="sm"
                        onClick={() => void decide(question, false)}
                        disabled={busyId === question.id}
                      >
                        Discard
                      </PushButton>
                    </div>
                  )}
                </article>
              ))}
            </div>
          </div>
        </section>
      )}
    </>
  );
}
