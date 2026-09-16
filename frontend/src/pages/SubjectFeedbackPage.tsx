import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { deleteSubtopicGoal, getSubjectFeedback, setSubtopicGoal } from "../api/feedback";
import type {
  GoalOut,
  RegressionOut,
  SubjectFeedbackDashboard,
  SubtopicAttemptScore,
  SubtopicFeedback,
} from "../api/types";
import { ApiError } from "../api/types";
import { Crumbs } from "../components/Crumbs";

function pct(value: string | number | null): number | null {
  if (value === null || value === undefined) return null;
  return typeof value === "string" ? Number(value) : value;
}

function AttemptChip({
  attempt,
  previousPercent,
}: {
  attempt: SubtopicAttemptScore;
  previousPercent: number | null;
}) {
  const percent = pct(attempt.percent) ?? 0;
  const trend = previousPercent === null ? "neutral" : percent >= previousPercent ? "up" : "down";
  const bg =
    trend === "up" ? "var(--success-bg)" : trend === "down" ? "var(--danger-bg)" : "var(--surface)";
  const border =
    trend === "up"
      ? "var(--success-border)"
      : trend === "down"
        ? "var(--danger-border)"
        : "var(--border)";

  return (
    <span
      style={{
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "4px 10px",
        borderRadius: "8px",
        background: bg,
        border: `1px solid ${border}`,
        minWidth: "64px",
      }}
    >
      <span style={{ fontWeight: 700, fontSize: "0.95rem" }}>{Math.round(percent)}%</span>
      <span style={{ fontSize: "0.65rem", color: "var(--ink-muted)" }}>
        Attempt {attempt.sequence}
      </span>
    </span>
  );
}

function formatDueDate(dueAt: string): string {
  try {
    return new Date(dueAt).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return dueAt;
  }
}

function GoalSection({
  subtopic,
  onGoalChange,
}: {
  subtopic: SubtopicFeedback;
  onGoalChange: (subtopicId: string, goal: GoalOut | null) => void;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const [targetPercent, setTargetPercent] = useState("80");
  const [dueDate, setDueDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const goal = subtopic.goal;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await setSubtopicGoal(subtopic.subtopic_id, {
        target_percent: Number(targetPercent),
        due_at: `${dueDate}T23:59:59.000Z`,
      });
      onGoalChange(subtopic.subtopic_id, result);
      setFormOpen(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save goal.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove() {
    setBusy(true);
    setError(null);
    try {
      await deleteSubtopicGoal(subtopic.subtopic_id);
      onGoalChange(subtopic.subtopic_id, null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove goal.");
    } finally {
      setBusy(false);
    }
  }

  if (goal) {
    const badgeClass =
      goal.status === "achieved"
        ? "badge--ok"
        : goal.status === "missed"
          ? "badge--warn"
          : "badge--info";
    const statusLabel =
      goal.status === "achieved" ? "Achieved" : goal.status === "missed" ? "Missed" : "On track";
    return (
      <div style={{ marginTop: "10px", paddingTop: "10px", borderTop: "1px solid var(--border)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          <span className={`badge ${badgeClass}`}>{statusLabel}</span>
          <span style={{ fontSize: "0.85rem", color: "var(--ink-muted)" }}>
            Goal: {pct(goal.target_percent)}% by {formatDueDate(goal.due_at)}
          </span>
        </div>
        {error && (
          <p className="form__error" role="alert" style={{ fontSize: "0.8rem", marginTop: "4px" }}>
            {error}
          </p>
        )}
        <button
          type="button"
          className="btn btn--sm"
          style={{ marginTop: "8px" }}
          disabled={busy}
          onClick={() => void handleRemove()}
        >
          {busy ? "Removing…" : "Remove goal"}
        </button>
      </div>
    );
  }

  return (
    <div style={{ marginTop: "10px", paddingTop: "10px", borderTop: "1px solid var(--border)" }}>
      {!formOpen ? (
        <button type="button" className="btn btn--sm" onClick={() => setFormOpen(true)}>
          Set a goal
        </button>
      ) : (
        <form
          onSubmit={(event) => void handleSubmit(event)}
          style={{ display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center" }}
        >
          <label style={{ fontSize: "0.8rem" }}>
            Target %
            <input
              type="number"
              min={0}
              max={100}
              required
              value={targetPercent}
              onChange={(event) => setTargetPercent(event.target.value)}
              style={{ width: "64px", marginLeft: "6px" }}
            />
          </label>
          <label style={{ fontSize: "0.8rem" }}>
            Due by
            <input
              type="date"
              required
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
              style={{ marginLeft: "6px" }}
            />
          </label>
          <button type="submit" className="btn btn--sm" disabled={busy || !dueDate}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={busy}
            onClick={() => setFormOpen(false)}
          >
            Cancel
          </button>
          {error && (
            <p className="form__error" role="alert" style={{ fontSize: "0.8rem", width: "100%" }}>
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

function SubtopicCard({
  subtopic,
  subjectId,
  regression,
  onGoalChange,
}: {
  subtopic: SubtopicFeedback;
  subjectId: string;
  regression: RegressionOut | undefined;
  onGoalChange: (subtopicId: string, goal: GoalOut | null) => void;
}) {
  const badgeClass =
    subtopic.is_weak === true ? "badge--warn" : subtopic.is_weak === false ? "badge--ok" : "badge--info";
  const status =
    subtopic.is_weak === true ? "Focus area" : subtopic.is_weak === false ? "Strong" : "Not enough data";
  const delta = pct(subtopic.delta);
  const remediation = subtopic.remediation;
  const attempts = subtopic.attempts;
  const lastIndex = attempts.length - 1;

  return (
    <div className="card" style={{ cursor: "default" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: "8px" }}>
        <h2>{subtopic.subtopic_name}</h2>
        <span className={`badge ${badgeClass}`}>{status}</span>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginTop: "12px" }}>
        {attempts.map((attempt, index) => {
          const chip = (
            <AttemptChip
              attempt={attempt}
              previousPercent={index > 0 ? pct(attempts[index - 1].percent) : null}
            />
          );
          if (index !== lastIndex || attempts.length < 2) {
            return <span key={attempt.sequence}>{chip}</span>;
          }
          return (
            <details key={attempt.sequence}>
              <summary style={{ cursor: "pointer" }}>{chip}</summary>
              <div style={{ marginTop: "8px", fontSize: "0.85rem", color: "var(--ink-muted)" }}>
                {delta !== null && (
                  <p style={{ margin: 0 }}>
                    {delta >= 0 ? "+" : ""}
                    {Math.round(delta)}% since Attempt {attempts.length - 1}
                  </p>
                )}
                {regression && (
                  <p style={{ margin: 0 }}>
                    {regression.regressed_question_count} question
                    {regression.regressed_question_count === 1 ? "" : "s"} you got right before
                    {regression.regressed_question_count === 1 ? " is" : " are"} now wrong.
                  </p>
                )}
              </div>
            </details>
          );
        })}
      </div>

      <p className="muted" style={{ fontSize: "0.85rem", marginTop: "10px" }}>
        {subtopic.question_count} question{subtopic.question_count === 1 ? "" : "s"}
        {subtopic.class_sample_size > 0 &&
          pct(subtopic.class_avg_percent) !== null &&
          ` · class avg ${Math.round(pct(subtopic.class_avg_percent) ?? 0)}%`}
      </p>

      {remediation && (remediation.material_title || remediation.retake_quiz_id) && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginTop: "10px" }}>
          {remediation.material_title && (
            <Link
              to={`/subjects/${subjectId}/subtopics/${subtopic.subtopic_id}/lesson`}
              className="btn btn--sm"
            >
              Review lesson: {remediation.material_title}
            </Link>
          )}
          {remediation.retake_quiz_id && (
            <Link to={`/quizzes/${remediation.retake_quiz_id}`} className="btn btn--sm">
              Retake this subtopic
            </Link>
          )}
        </div>
      )}

      <GoalSection subtopic={subtopic} onGoalChange={onGoalChange} />
    </div>
  );
}

export function SubjectFeedbackPage() {
  const { subjectId = "" } = useParams();
  const [dashboard, setDashboard] = useState<SubjectFeedbackDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDashboard(null);
    setError(null);
    void (async () => {
      try {
        const data = await getSubjectFeedback(subjectId);
        if (!cancelled) setDashboard(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load feedback.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [subjectId]);

  const regressionsBySubtopic = new Map(
    (dashboard?.regressions ?? []).map((r) => [r.subtopic_id, r]),
  );

  function handleGoalChange(subtopicId: string, goal: GoalOut | null) {
    setDashboard((prev) =>
      prev
        ? {
            ...prev,
            subtopics: prev.subtopics.map((s) =>
              s.subtopic_id === subtopicId ? { ...s, goal } : s,
            ),
          }
        : prev,
    );
  }

  return (
    <>
      <Crumbs
        parts={[
          { label: "Feedback", to: "/feedback" },
          { label: dashboard?.subject_name ?? "Subject" },
        ]}
      />

      {!dashboard && !error && (
        <div className="center-state" role="status">
          Loading feedback…
        </div>
      )}

      {error && (
        <div className="center-state">
          <p className="form__error" role="alert">
            {error}
          </p>
        </div>
      )}

      {dashboard && (
        <div>
          <header className="page-head">
            <p className="kicker">Feedback · {dashboard.subject_name}</p>
            <h1>Your personalized feedback</h1>
          </header>

          {dashboard.attempt_count === 0 ? (
            <div className="alert alert--info">
              No scored quiz attempts yet in this subject. Take a quiz to see feedback here.
            </div>
          ) : (
            <>
              <div className="alert alert--info">
                <p className="score-hero__status">{dashboard.summary}</p>
                <p className="score-hero__meta">
                  Across {dashboard.attempt_count} scored attempt
                  {dashboard.attempt_count === 1 ? "" : "s"}
                </p>
              </div>

              <div className="grid grid--2" style={{ marginTop: "1rem" }}>
                {dashboard.subtopics.map((s) => (
                  <SubtopicCard
                    key={s.subtopic_id}
                    subtopic={s}
                    subjectId={subjectId}
                    regression={regressionsBySubtopic.get(s.subtopic_id)}
                    onGoalChange={handleGoalChange}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}
