import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getSubjectEngagementRates, type EngagementRate } from "../api/engagement";
import { getFeedbackSubject } from "../api/feedback";
import { getSelfEvaluation, recordSelfEvaluation, type ConfidenceLevel } from "../api/selfEvaluation";
import type { FeedbackSubjectDetail, FeedbackTopicScore } from "../api/types";
import { ApiError } from "../api/types";
import { Crumbs } from "../components/Crumbs";
import {
  AchievementBanner,
  FocusLine,
  Milestone,
  SubjectEngagement,
} from "../components/FeedbackExtras";
import {
  attemptBadge,
  calibrationMessage,
  formatScore,
  formatTrend,
  scoreNumber,
} from "../lib/feedbackHierarchy";

function TopicCard({ subjectId, topic }: { subjectId: string; topic: FeedbackTopicScore }) {
  const trend = formatTrend(topic.trend, topic.trend_delta);
  const attempted = topic.badge !== "not_attempted";
  return (
    <Link
      to={`/subjects/${subjectId}/feedback/topics/${topic.topic_id}`}
      className="card feedback-card"
    >
      <div className="meta-row">
        <h2>{topic.topic_name}</h2>
        <span className={`badge ${topic.badge === "passed" ? "badge--ok" : "badge--info"}`}>
          {attemptBadge(topic.badge, topic.attempt_count, topic.best_score_percent)}
        </span>
      </div>
      <p className="feedback-card__score">{formatScore(topic.best_score_percent)}</p>
      <p className="progress-label">Best score</p>
      {attempted && (
        <p className="progress-label">Latest {formatScore(topic.latest_score_percent)}</p>
      )}
      {trend && <p className="feedback-card__trend">{trend}</p>}
    </Link>
  );
}

function SelfCheck({
  subjectId,
  subjectName,
  score,
  initial,
}: {
  subjectId: string;
  subjectName: string;
  score: number | null;
  initial: ConfidenceLevel | null;
}) {
  const [confidence, setConfidence] = useState<ConfidenceLevel | null>(initial);

  useEffect(() => {
    setConfidence(initial);
  }, [initial]);

  async function choose(level: ConfidenceLevel) {
    setConfidence(level);
    await recordSelfEvaluation(subjectId, level);
  }

  const options: { value: ConfidenceLevel; label: string }[] = [
    { value: "still_learning", label: "Still learning" },
    { value: "getting_it", label: "Getting it" },
    { value: "confident", label: "Confident" },
  ];

  return (
    <section className="feedback-self-check" aria-label={`Self-check for ${subjectName}`}>
      <p>How well do you feel you understand {subjectName}?</p>
      <div className="feedback-self-check__options">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className="btn btn--sm"
            aria-pressed={confidence === option.value}
            onClick={() => void choose(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
      {confidence && <p className="feedback-self-check__note">{calibrationMessage(confidence, score)}</p>}
    </section>
  );
}

export function SubjectFeedbackPage() {
  const { subjectId = "" } = useParams();
  const [detail, setDetail] = useState<FeedbackSubjectDetail | null>(null);
  const [confidence, setConfidence] = useState<ConfidenceLevel | null>(null);
  const [engagement, setEngagement] = useState<EngagementRate[]>([]);
  const [showReport, setShowReport] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getFeedbackSubject(subjectId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load feedback.");
        }
      });
    void getSelfEvaluation(subjectId).then((row) => {
      if (!cancelled) setConfidence(row?.confidence ?? null);
    });
    void getSubjectEngagementRates(subjectId).then((rates) => {
      if (!cancelled) setEngagement(rates);
    });
    return () => {
      cancelled = true;
    };
  }, [subjectId]);

  if (!detail && !error) {
    return (
      <p className="muted" role="status">
        Loading feedback…
      </p>
    );
  }
  if (error || !detail) {
    return (
      <p className="form__error" role="alert">
        {error ?? "Could not load feedback."}
      </p>
    );
  }

  const trend = formatTrend(detail.trend, detail.trend_delta);

  return (
    <div className="feedback-view">
      <Crumbs
        parts={[
          { label: "Feedback", to: "/feedback" },
          { label: detail.subject_name },
        ]}
      />
      <header className="page-head">
        <p className="kicker">
          {detail.grade_name} · {detail.subject_name}
        </p>
        <h1>{detail.subject_name}</h1>
      </header>

      <section className="feedback-summary" aria-label="Subject summary">
        <p className="feedback-summary__kicker">Subject</p>
        <p className="feedback-card__score">{formatScore(detail.score_percent)}</p>
        {trend && <p className="feedback-card__trend">{trend}</p>}
        {detail.topics_needing_attention.length > 0 && (
          <p className="progress-label">
            Needs attention: {detail.topics_needing_attention.join(", ")}
          </p>
        )}
        <Milestone mastered={detail.concepts_mastered} attempted={detail.concepts_attempted} />
        <SubjectEngagement rates={engagement} />
        <FocusLine focus={detail.focus} />
        <AchievementBanner tier={detail.achievement} />
        <div className="feedback-summary__text">
          <p>{detail.summary}</p>
        </div>
      </section>

      <div className="feedback-grid">
        {detail.topics.map((topic) => (
          <TopicCard key={topic.topic_id} subjectId={subjectId} topic={topic} />
        ))}
      </div>

      <SelfCheck
        subjectId={subjectId}
        subjectName={detail.subject_name}
        score={scoreNumber(detail.score_percent)}
        initial={confidence}
      />

      <div className="feedback-report">
        <button type="button" className="btn btn--sm" onClick={() => setShowReport((open) => !open)}>
          {showReport ? "Hide report card" : "View report card"}
        </button>
        {showReport && (
          <div className="feedback-report__sheet">
            <div className="feedback-report__head">
              <h2>Report card</h2>
              <button type="button" className="btn btn--sm" onClick={() => window.print()}>
                Download / Print
              </button>
            </div>
            <p className="progress-label">{detail.subject_name}</p>
            <table>
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Best</th>
                  <th>Attempts</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {detail.topics.map((topic) => (
                  <tr key={topic.topic_id}>
                    <td>{topic.topic_name}</td>
                    <td>{formatScore(topic.best_score_percent)}</td>
                    <td>{topic.attempt_count}</td>
                    <td>{attemptBadge(topic.badge, topic.attempt_count, topic.best_score_percent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
