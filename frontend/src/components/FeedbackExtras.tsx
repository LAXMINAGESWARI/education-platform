import { useState, type FormEvent } from "react";

import { deleteTopicGoal, setTopicGoal } from "../api/feedback";
import type { EngagementRate } from "../api/engagement";
import type { FeedbackAchievement, FeedbackFocus, GoalOut } from "../api/types";
import { ApiError } from "../api/types";
import { achievementCopy, focusSentence, scoreNumber } from "../lib/feedbackHierarchy";

export function Milestone({ mastered, attempted }: { mastered: number; attempted: number }) {
  if (attempted <= 0) return null;
  return (
    <p className="feedback-milestone">
      {mastered} of {attempted} concepts mastered
    </p>
  );
}

export function FocusLine({ focus }: { focus: FeedbackFocus | null }) {
  if (!focus || focus.gap_percent <= 0) return null;
  return <p className="feedback-focus">{focusSentence(focus.gap_percent, focus.topic_name)}</p>;
}

export function AchievementBanner({ tier }: { tier: FeedbackAchievement | null }) {
  if (!tier) return null;
  const copy = achievementCopy(tier);
  return (
    <div className={`feedback-achievement feedback-achievement--${tier}`} role="status">
      <span aria-hidden="true">{copy.emoji}</span>
      <div>
        <p className="feedback-achievement__label">{copy.label}</p>
        <p>{copy.detail}</p>
      </div>
    </div>
  );
}

export function SubjectEngagement({ rates }: { rates: EngagementRate[] }) {
  const usable = rates.filter((rate) => rate.engagement_rate !== null && rate.total_slides > 0);
  if (usable.length === 0) return null;
  const engaged = usable.reduce((sum, rate) => sum + rate.engaged_slides, 0);
  const total = usable.reduce((sum, rate) => sum + rate.total_slides, 0);
  if (total <= 0) return null;
  const rate = engaged / total;
  const percent = Math.round(rate * 100);
  const tier =
    rate >= 0.7
      ? { label: "Focused", className: "feedback-engagement--focused" }
      : rate >= 0.4
        ? { label: "Skimmed", className: "feedback-engagement--skimmed" }
        : { label: "Quick pass", className: "feedback-engagement--quick" };
  return (
    <p className={`feedback-engagement ${tier.className}`}>
      {percent}% engaged · {tier.label} ({engaged}/{total} slides)
    </p>
  );
}

function formatDueDate(dueAt: string): string {
  const parsed = new Date(dueAt);
  if (Number.isNaN(parsed.getTime())) return dueAt;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function GoalSection({
  topicId,
  topicName,
  goal,
  bestScore,
  onGoalChange,
}: {
  topicId: string;
  topicName: string;
  goal: GoalOut | null;
  bestScore: string | number | null;
  onGoalChange: (goal: GoalOut | null) => void;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const [targetPercent, setTargetPercent] = useState("80");
  const [dueDate, setDueDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await setTopicGoal(topicId, {
        target_percent: Number(targetPercent),
        due_at: `${dueDate}T23:59:59.000Z`,
      });
      onGoalChange(result);
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
      await deleteTopicGoal(topicId);
      onGoalChange(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove goal.");
    } finally {
      setBusy(false);
    }
  }

  if (goal) {
    const latest = scoreNumber(bestScore);
    const target = scoreNumber(goal.target_percent) ?? 0;
    const status = latest !== null && latest >= target ? "achieved" : goal.status;
    const badgeClass =
      status === "achieved" ? "badge--ok" : status === "missed" ? "badge--warn" : "badge--info";
    const statusLabel =
      status === "achieved" ? "Achieved ✓" : status === "missed" ? "Missed" : "On track";
    return (
      <div className="feedback-goal">
        <span className={`badge ${badgeClass}`}>{statusLabel}</span>
        <span className="feedback-goal__target">
          Goal: {Math.round(target)}% by {formatDueDate(goal.due_at)}
        </span>
        {error && (
          <p className="form__error" role="alert">
            {error}
          </p>
        )}
        <button type="button" className="btn btn--sm" disabled={busy} onClick={() => void handleRemove()}>
          {busy ? "Removing…" : "Remove goal"}
        </button>
      </div>
    );
  }

  return (
    <div className="feedback-goal">
      {!formOpen ? (
        <button type="button" className="btn btn--sm" onClick={() => setFormOpen(true)}>
          Set a goal
        </button>
      ) : (
        <form onSubmit={(event) => void handleSubmit(event)} className="feedback-goal__form">
          <label>
            Target %
            <input
              type="number"
              min={0}
              max={100}
              required
              value={targetPercent}
              onChange={(event) => setTargetPercent(event.target.value)}
              aria-label={`Target percent for ${topicName}`}
            />
          </label>
          <label>
            Due by
            <input
              type="date"
              required
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
              aria-label={`Due date for ${topicName}`}
            />
          </label>
          <button type="submit" className="btn btn--sm" disabled={busy || !dueDate}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button type="button" className="btn btn--sm" disabled={busy} onClick={() => setFormOpen(false)}>
            Cancel
          </button>
          {error && (
            <p className="form__error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}
