import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getFeedbackTopic } from "../api/feedback";
import type { FeedbackTopicDetail, GoalOut } from "../api/types";
import { ApiError } from "../api/types";
import { Crumbs } from "../components/Crumbs";
import { GoalSection, Milestone } from "../components/FeedbackExtras";
import { ScoreMetrics } from "../components/ScoreMetrics";
import { attemptBadge, formatScore, formatTrend } from "../lib/feedbackHierarchy";

export function TopicFeedbackPage() {
  const { subjectId = "", topicId = "" } = useParams();
  const [detail, setDetail] = useState<FeedbackTopicDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getFeedbackTopic(topicId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load this topic.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [topicId]);

  if (!detail && !error) {
    return (
      <p className="muted" role="status">
        Loading topic feedback…
      </p>
    );
  }
  if (error || !detail) {
    return (
      <p className="form__error" role="alert">
        {error ?? "Could not load this topic."}
      </p>
    );
  }

  function changeGoal(goal: GoalOut | null) {
    setDetail((current) => (current ? { ...current, goal } : current));
  }

  const trend = formatTrend(detail.trend, detail.trend_delta);

  return (
    <div className="feedback-view">
      <Crumbs
        parts={[
          { label: "Feedback", to: "/feedback" },
          { label: detail.subject_name, to: `/subjects/${subjectId}/feedback` },
          { label: detail.topic_name },
        ]}
      />
      <header className="page-head">
        <p className="kicker">{detail.subject_name}</p>
        <h1>{detail.topic_name}</h1>
        <p>
          <span className={`badge ${detail.badge === "passed" ? "badge--ok" : "badge--info"}`}>
            {attemptBadge(detail.badge, detail.attempt_count, detail.best_score_percent)}
          </span>
        </p>
      </header>

      <section className="feedback-summary" aria-label="Topic summary">
        <p className="feedback-summary__kicker">Topic quiz</p>
        <p className="feedback-card__score">{formatScore(detail.best_score_percent)}</p>
        <p className="progress-label">
          {detail.attempt_count === 0
            ? "Not attempted"
            : `Best score · latest ${formatScore(detail.latest_score_percent)}`}
        </p>
        {trend && <p className="feedback-card__trend">{trend}</p>}
        <Milestone mastered={detail.concepts_mastered} attempted={detail.concepts_attempted} />
        <GoalSection
          topicId={detail.topic_id}
          topicName={detail.topic_name}
          goal={detail.goal}
          bestScore={detail.best_score_percent}
          onGoalChange={changeGoal}
        />
        <div className="feedback-summary__text">
          <p>{detail.summary}</p>
        </div>
        <div className="meta-row">
          {detail.has_lesson && (
            <Link to={`/subjects/${subjectId}/topics/${topicId}/lesson`} className="btn btn--sm">
              Review the lesson
            </Link>
          )}
          {detail.quiz_id && (
            <Link to={`/quizzes/${detail.quiz_id}`} className="btn btn--sm btn--primary">
              {detail.attempt_count > 0 ? "Retake the quiz" : "Take the quiz"}
            </Link>
          )}
        </div>
      </section>

      <ScoreMetrics detail={detail} />
    </div>
  );
}
