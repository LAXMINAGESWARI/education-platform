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

const PASS_THRESHOLD = 70;

function attemptTitle(
  attempts: number,
  bestScore: number,
): { label: string; emoji: string; color: string } {
  const band: "perfect" | "high" | "pass" =
    bestScore === 100 ? "perfect" : bestScore >= 90 ? "high" : "pass";

  const titles = {
    perfect: [
      { label: "Flawless",   emoji: "👑", color: "#92400e" },
      { label: "Diamond",    emoji: "💎", color: "#0e7490" },
      { label: "Tenacious",  emoji: "🏅", color: "#7c3aed" },
      { label: "Champion",   emoji: "🥇", color: "#b45309" },
      { label: "Legendary",  emoji: "🏆", color: "#be185d" },
    ],
    high: [
      { label: "Star Performer", emoji: "🌟", color: "#166534" },
      { label: "Sharp Mind",     emoji: "🔥", color: "#991b1b" },
      { label: "Rising Star",    emoji: "🚀", color: "#1e40af" },
      { label: "Breakthrough",   emoji: "💡", color: "#b45309" },
      { label: "Hard Earned",    emoji: "⭐", color: "#7c3aed" },
    ],
    pass: [
      { label: "First Try!",     emoji: "🎯", color: "#166534" },
      { label: "Quick Learner",  emoji: "⚡", color: "#1e40af" },
      { label: "Persistent",     emoji: "💪", color: "#7c3aed" },
      { label: "Determined",     emoji: "🔄", color: "#b45309" },
      { label: "Never Give Up",  emoji: "🌱", color: "#be185d" },
    ],
  };

  const index = Math.min(attempts - 1, 4);
  return titles[band][index];
}

// ─── NEW: Compute overall score (best score per subtopic, averaged) ───────────
function computeOverallScore(subtopics: SubtopicFeedback[]): number | null {
  const scores: number[] = [];
  for (const s of subtopics) {
    if (s.attempts.length === 0) continue;
    const best = Math.max(...s.attempts.map((a) => pct(a.percent) ?? 0));
    scores.push(best);
  }
  if (scores.length === 0) return null;
  return scores.reduce((a, b) => a + b, 0) / scores.length;
}

// ─── NEW: Overall Score Hero ──────────────────────────────────────────────────
function ScoreHero({
  dashboard,
}: {
  dashboard: SubjectFeedbackDashboard;
}) {
  const overallScore = computeOverallScore(dashboard.subtopics);

  const weakCount = dashboard.subtopics.filter((s) => {
    if (s.attempts.length === 0) return false;
    return Math.max(...s.attempts.map((a) => pct(a.percent) ?? 0)) < PASS_THRESHOLD;
  }).length;
  const noDataCount = dashboard.subtopics.filter((s) => s.attempts.length === 0).length;
  const passedSubtopics = dashboard.subtopics.filter((s) => {
    if (s.attempts.length === 0) return false;
    return Math.max(...s.attempts.map((a) => pct(a.percent) ?? 0)) >= PASS_THRESHOLD;
  });

  const scoreColor =
    overallScore === null
      ? "var(--ink-muted)"
      : overallScore >= 70
        ? "var(--success-border)"
        : "var(--danger-border)";

  const message =
    overallScore === null
      ? "Keep going — more attempts unlock your score."
      : overallScore >= 80
        ? "Great work! You're performing well overall."
        : overallScore >= 60
          ? "You're making progress — focus on weak areas to level up."
          : "Keep pushing — review the focus areas below to improve.";

  return (
    <div
      style={{
        display: "flex",
        gap: "1.5rem",
        alignItems: "center",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "12px",
        padding: "1.25rem 1.5rem",
        marginBottom: "1.25rem",
        flexWrap: "wrap",
      }}
    >
      {/* Big score circle */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          width: "90px",
          height: "90px",
          borderRadius: "50%",
          border: `4px solid ${scoreColor}`,
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: "1.6rem", fontWeight: 800, color: scoreColor, lineHeight: 1 }}>
          {overallScore !== null ? `${Math.round(overallScore)}%` : "—"}
        </span>
        <span style={{ fontSize: "0.6rem", color: "var(--ink-muted)", marginTop: "2px" }}>
          best score
        </span>
      </div>

      {/* Stats + message */}
      <div style={{ flex: 1, minWidth: "180px" }}>
        <p style={{ margin: "0 0 6px 0", fontWeight: 600, fontSize: "0.95rem" }}>{message}</p>
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
          {passedSubtopics.map((s) => {
            const best = Math.max(...s.attempts.map((a) => pct(a.percent) ?? 0));
            const t = attemptTitle(s.attempts.length, best);
            return (
              <span key={s.subtopic_id} style={{ fontSize: "0.82rem", color: t.color, fontWeight: 600 }}>
                {t.emoji} {s.subtopic_name}: {t.label}
              </span>
            );
          })}
          {weakCount > 0 && (
            <span style={{ fontSize: "0.82rem", color: "var(--danger-border)", fontWeight: 600 }}>
              ❌ {weakCount} needs work
            </span>
          )}
          {noDataCount > 0 && (
            <span style={{ fontSize: "0.82rem", color: "var(--ink-muted)" }}>
              · {noDataCount} no data yet
            </span>
          )}
        </div>
        <p style={{ margin: "6px 0 0 0", fontSize: "0.8rem", color: "var(--ink-muted)" }}>
          {dashboard.attempt_count === 1
            ? "1 attempt so far"
            : `Achieved in ${dashboard.attempt_count} attempts`}
        </p>
      </div>
    </div>
  );
}


// ─── NEW: Focus / Achievement Banner ─────────────────────────────────────────
function FocusBanner({
  weakSubtopics,
  overallScore,
}: {
  weakSubtopics: SubtopicFeedback[];
  overallScore: number | null;
}) {
  // Still has subtopics to work on — show focus list
  if (weakSubtopics.length > 0) {
    return (
      <div
        style={{
          background: "var(--danger-bg)",
          border: "1px solid var(--danger-border)",
          borderRadius: "10px",
          padding: "0.9rem 1.1rem",
          marginBottom: "1.25rem",
        }}
      >
        <p style={{ margin: "0 0 6px 0", fontWeight: 700, fontSize: "0.9rem", color: "var(--danger-border)" }}>
          🎯 Focus on these first
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
          {weakSubtopics.map((s) => (
            <span
              key={s.subtopic_id}
              style={{
                background: "var(--surface)",
                border: "1px solid var(--danger-border)",
                borderRadius: "6px",
                padding: "3px 10px",
                fontSize: "0.82rem",
                fontWeight: 500,
              }}
            >
              {s.subtopic_name}
            </span>
          ))}
        </div>
      </div>
    );
  }

  // All subtopics passed — show tiered achievement banner
  const score = overallScore ?? 0;
  const tier =
    score === 100
      ? { emoji: "🏆", label: "Mastery!", sub: "Perfect score — you've completely nailed this subject!", color: "#78350f", bg: "#fffbeb", border: "#d97706" }
      : score >= 90
        ? { emoji: "🔥", label: "Advanced!", sub: "Excellent work — you're well above the bar!", color: "#312e81", bg: "#eef2ff", border: "#4f46e5" }
        : score >= 80
          ? { emoji: "⭐", label: "Proficient!", sub: "Solid understanding across all subtopics!", color: "#6b21a8", bg: "#faf5ff", border: "#9333ea" }
          : { emoji: "🌱", label: "On Track!", sub: "You're passing — keep pushing to go higher!", color: "#0f766e", bg: "#f0fdfa", border: "#0d9488" };

  return (
    <div
      style={{
        background: tier.bg,
        border: `1px solid ${tier.border}`,
        borderRadius: "10px",
        padding: "0.9rem 1.1rem",
        marginBottom: "1.25rem",
        display: "flex",
        alignItems: "center",
        gap: "12px",
      }}
    >
      <span style={{ fontSize: "1.8rem" }}>{tier.emoji}</span>
      <div>
        <p style={{ margin: "0 0 2px 0", fontWeight: 800, fontSize: "1rem", color: tier.color }}>
          {tier.label}
        </p>
        <p style={{ margin: 0, fontSize: "0.82rem", color: tier.color }}>{tier.sub}</p>
      </div>
    </div>
  );
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
    // Override status if latest attempt already meets the target
    const latestScore =
      subtopic.attempts.length > 0
        ? (pct(subtopic.attempts[subtopic.attempts.length - 1].percent) ?? 0)
        : 0;
    const effectiveStatus =
      latestScore >= (pct(goal.target_percent) ?? 0) ? "achieved" : goal.status;

    const badgeClass =
      effectiveStatus === "achieved"
        ? "badge--ok"
        : effectiveStatus === "missed"
          ? "badge--warn"
          : "badge--info";
    const statusLabel =
      effectiveStatus === "achieved" ? "Achieved ✓" : effectiveStatus === "missed" ? "Missed" : "On track";
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
  const attempts = subtopic.attempts;
  const delta = pct(subtopic.delta);
  const remediation = subtopic.remediation;

  const bestScore = attempts.length > 0 ? Math.max(...attempts.map((a) => pct(a.percent) ?? 0)) : null;
  const passed = bestScore !== null && bestScore >= PASS_THRESHOLD;
  const title = passed && bestScore !== null ? attemptTitle(attempts.length, bestScore) : null;
  const badgeClass = passed ? "badge--ok" : bestScore === null ? "badge--info" : "badge--warn";
  const status = passed ? `${title!.emoji} ${title!.label}` : bestScore === null ? "No data yet" : "Needs work";
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

  // ─── NEW: Sort weak subtopics first ────────────────────────────────────────
  const getCardStatus = (s: SubtopicFeedback) => {
    if (s.attempts.length === 0) return "no-data";
    const best = Math.max(...s.attempts.map((a) => pct(a.percent) ?? 0));
    return best < PASS_THRESHOLD ? "weak" : "passed";
  };

  const sortedSubtopics = dashboard
    ? [...dashboard.subtopics].sort((a, b) => {
        const rank = (s: SubtopicFeedback) => {
          const st = getCardStatus(s);
          return st === "weak" ? 0 : st === "no-data" ? 1 : 2;
        };
        return rank(a) - rank(b);
      })
    : [];

  const overallScore = computeOverallScore(sortedSubtopics);

  const FOCUS_THRESHOLD = 70;
  const weakSubtopics = sortedSubtopics.filter((s) => {
    if (s.attempts.length === 0) return false;
    return Math.max(...s.attempts.map((a) => pct(a.percent) ?? 0)) < FOCUS_THRESHOLD;
  });

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
              {/* ─── NEW: Score Hero ──────────────────────────────────────── */}
              <ScoreHero dashboard={dashboard} />

              {/* ─── NEW: Focus / Achievement Banner ────────────────────── */}
              <FocusBanner weakSubtopics={weakSubtopics} overallScore={overallScore} />

              {/* ─── Subtopic grid (weak first) ──────────────────────────── */}
              <div className="grid grid--2" style={{ marginTop: "1rem" }}>
                {sortedSubtopics.map((s) => (
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
