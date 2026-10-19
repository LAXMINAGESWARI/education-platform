import type { FeedbackDifficulty, FeedbackTopicDetail } from "../api/types";
import { bandLabel, formatDuration, formatScore, formatTrend, scoreNumber } from "../lib/feedbackHierarchy";

function countLine(correct: number, answered: number, percent: string | number): string {
  return `${correct} of ${answered} correct, ${Math.round(scoreNumber(percent) ?? 0)}%`;
}

function difficultyLabel(level: FeedbackDifficulty): string {
  switch (level) {
    case "easy":
      return "Easy";
    case "medium":
      return "Medium";
    case "hard":
      return "Hard";
    default: {
      const unreachable: never = level;
      return unreachable;
    }
  }
}

export function ScoreMetrics({ detail }: { detail: FeedbackTopicDetail }) {
  const hasAttempts = detail.attempt_count > 0 && scoreNumber(detail.best_score_percent) !== null;
  const change = formatTrend(detail.trend, detail.trend_delta);
  const concepts = [...detail.subtopics].sort((left, right) => {
    const leftPercent = scoreNumber(left.percent) ?? 0;
    const rightPercent = scoreNumber(right.percent) ?? 0;
    if (leftPercent !== rightPercent) return leftPercent - rightPercent;
    return left.name.localeCompare(right.name);
  });

  return (
    <section className="feedback-metrics-section" aria-label="Score and metrics">
      <h2 className="section-title">Score and metrics</h2>
      {hasAttempts ? (
        <dl className="feedback-metrics">
          <div>
            <dt>Attempts</dt>
            <dd>{detail.attempt_count}</dd>
          </div>
          <div>
            <dt>Best score</dt>
            <dd>{formatScore(detail.best_score_percent)}</dd>
          </div>
          <div>
            <dt>Latest score</dt>
            <dd>{formatScore(detail.latest_score_percent)}</dd>
          </div>
          {detail.average_score_percent !== null && (
            <div>
              <dt>Average score</dt>
              <dd>{formatScore(detail.average_score_percent)}</dd>
            </div>
          )}
          {change && (
            <div>
              <dt>Since the previous attempt</dt>
              <dd>{change}</dd>
            </div>
          )}
          {detail.questions_answered !== null &&
            detail.questions_correct !== null &&
            detail.accuracy_percent !== null && (
              <div>
                <dt>Questions</dt>
                <dd>
                  {countLine(
                    detail.questions_correct,
                    detail.questions_answered,
                    detail.accuracy_percent,
                  )}
                </dd>
              </div>
            )}
          {detail.time_spent_seconds !== null && (
            <div>
              <dt>Time spent</dt>
              <dd>{formatDuration(detail.time_spent_seconds)}</dd>
            </div>
          )}
        </dl>
      ) : (
        <p className="progress-label">No quiz attempts yet.</p>
      )}

      {detail.difficulty_accuracy.length > 0 && (
        <div className="feedback-difficulty">
          <h3 className="feedback-metrics__subhead">Accuracy by difficulty</h3>
          <ul aria-label="Accuracy by difficulty">
            {detail.difficulty_accuracy.map((row) => (
              <li key={row.difficulty}>
                <span>{difficultyLabel(row.difficulty)}</span>
                <span>{countLine(row.correct_count, row.answered_count, row.percent)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {concepts.length > 0 && (
        <>
          <h3 className="feedback-metrics__subhead">Concepts</h3>
          <ul className="feedback-concepts" aria-label="Concepts">
            {concepts.map((row) => {
              const percent = Math.max(0, Math.min(100, scoreNumber(row.percent) ?? 0));
              return (
                <li key={row.subtopic_id} className={`feedback-concept feedback-concept--${row.band}`}>
                  <span className="feedback-concept__name">{row.name}</span>
                  <span className="feedback-concept__meta">
                    {countLine(row.correct_count, row.answered_count, row.percent)}
                  </span>
                  <span className="feedback-bar" aria-hidden="true">
                    <span style={{ width: `${percent}%` }} />
                  </span>
                  <span className="badge">{bandLabel(row.band)}</span>
                </li>
              );
            })}
          </ul>
        </>
      )}
      {hasAttempts && concepts.length === 0 && (
        <p className="progress-label">
          No separate concepts to break out yet. The topic score includes every question.
        </p>
      )}
    </section>
  );
}
