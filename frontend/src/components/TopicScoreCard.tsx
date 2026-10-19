import { useState } from "react";
import { Link } from "react-router-dom";

import type { FeedbackTopicScore } from "../api/types";
import {
  badgeLabel,
  formatScore,
  formatTrend,
  goalStatusLabel,
  scoreNumber,
} from "../lib/feedbackHierarchy";
import { AttemptBadge } from "./FeedbackExtras";

function attemptChange(current: number, previous: number | null): string | null {
  if (previous === null) return null;
  const delta = Math.round(current - previous);
  if (delta === 0) return "no change";
  if (delta > 0) return `+${delta}`;
  return `${delta}`;
}

function formatDueDate(dueAt: string): string {
  const parsed = new Date(dueAt);
  if (Number.isNaN(parsed.getTime())) return dueAt;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function topicStatusLabel(topic: FeedbackTopicScore): string {
  if (topic.goal) return goalStatusLabel(topic.goal, topic.best_score_percent);
  return badgeLabel(topic.badge);
}

export function TopicScoreCard({
  subjectId,
  topic,
}: {
  subjectId: string;
  topic: FeedbackTopicScore;
}) {
  const [expanded, setExpanded] = useState(false);
  const best = scoreNumber(topic.best_score_percent);
  const latest = scoreNumber(topic.latest_score_percent);
  const delta = scoreNumber(topic.trend_delta);
  const isNewBest =
    topic.attempt_count >= 2 && best !== null && latest === best && (delta ?? 0) > 0;
  const scores = [...topic.attempt_scores].sort(
    (left, right) => left.attempt_number - right.attempt_number,
  );
  const strong = topic.concepts.filter((row) => row.band === "strong").length;
  const weak = topic.concepts.filter((row) => row.band === "needs_work").length;
  const trend = formatTrend(topic.trend, topic.trend_delta);
  const classAvg = scoreNumber(topic.class_avg_percent);
  const showClass = classAvg !== null && topic.class_sample_size > 0;
  const goalTarget = topic.goal ? scoreNumber(topic.goal.target_percent) : null;
  const goalLabel = topic.goal ? goalStatusLabel(topic.goal, topic.best_score_percent) : null;
  const goalTone =
    goalLabel === "Achieved ✓" ? "badge--ok" : goalLabel === "Missed" ? "badge--warn" : "badge--info";

  return (
    <article className="card feedback-card" aria-label={topic.topic_name}>
      <div className="meta-row">
        <h2>
          <Link to={`/subjects/${subjectId}/feedback/topics/${topic.topic_id}`}>
            {topic.topic_name}
          </Link>
        </h2>
        <AttemptBadge
          badge={topic.badge}
          attemptCount={topic.attempt_count}
          best={topic.best_score_percent}
        />
      </div>
      {best !== null && (
        <p className="feedback-card__score">
          {formatScore(topic.best_score_percent)}
          {isNewBest && <span className="feedback-new-best">↑ New best!</span>}
        </p>
      )}
      {best !== null && <p className="progress-label">Best score</p>}
      {latest !== null && (
        <p className="progress-label">Latest {formatScore(topic.latest_score_percent)}</p>
      )}
      {scores.length > 0 && (
        <ul className="feedback-attempt-chips" aria-label={`${topic.topic_name} attempts`}>
          {scores.map((attempt, index) => {
            const percent = scoreNumber(attempt.score_percent) ?? 0;
            const previous =
              index > 0 ? scoreNumber(scores[index - 1]?.score_percent ?? null) : null;
            const change = attemptChange(percent, previous);
            const tone =
              change === null || change === "no change"
                ? ""
                : change.startsWith("+")
                  ? "feedback-chip--up"
                  : "feedback-chip--down";
            return (
              <li key={attempt.attempt_number} className={`feedback-chip ${tone}`}>
                <strong>{Math.round(percent)}%</strong>
                <span>Attempt {attempt.attempt_number}</span>
                {change && <span>{change}</span>}
              </li>
            );
          })}
        </ul>
      )}
      {showClass && classAvg !== null && (
        <p className="progress-label">Class average {Math.round(classAvg)}%</p>
      )}
      {topic.regression_count !== null && topic.regression_count > 0 && (
        <p className="progress-label">
          {topic.regression_count} question{topic.regression_count === 1 ? "" : "s"} you got right
          before {topic.regression_count === 1 ? "is" : "are"} now wrong.
        </p>
      )}
      {topic.concepts.length > 0 && (
        <p className="progress-label">
          {strong} strong · {weak} needs work
        </p>
      )}
      {topic.goal && goalTarget !== null && goalLabel && (
        <p className="feedback-goal">
          <span className={`badge ${goalTone}`}>{goalLabel}</span>
          <span className="feedback-goal__target">
            Goal: {Math.round(goalTarget)}% by {formatDueDate(topic.goal.due_at)}
          </span>
        </p>
      )}
      <button
        type="button"
        className="btn btn--soft btn--sm"
        aria-expanded={expanded}
        onClick={() => setExpanded((open) => !open)}
      >
        {expanded ? "Hide details ▲" : "Show details ▼"}
      </button>
      {expanded && (
        <div className="feedback-card__details">
          {topic.concepts.length > 0 ? (
            <ul aria-label={`${topic.topic_name} concept scores`}>
              {topic.concepts.map((row) => (
                <li key={row.subtopic_id}>
                  {row.name} {formatScore(row.percent)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="progress-label">No concept scores for this topic yet.</p>
          )}
          {trend && <p>{trend}</p>}
        </div>
      )}
    </article>
  );
}
