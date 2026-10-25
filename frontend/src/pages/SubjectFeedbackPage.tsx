import { useEffect, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

import { getSubjectEngagementRates, type EngagementRate } from "../api/engagement";
import { getFeedbackSubject } from "../api/feedback";
import { getSelfEvaluation, recordSelfEvaluation, type ConfidenceLevel } from "../api/selfEvaluation";
import type { FeedbackSubjectDetail, FeedbackTopicScore } from "../api/types";
import { ApiError } from "../api/types";
import { Crumbs } from "../components/Crumbs";
import { AttemptBadge, AchievementBanner, FocusLine, Milestone, SubjectEngagement } from "../components/FeedbackExtras";
import { TopicScoreCard, topicStatusLabel } from "../components/TopicScoreCard";
import {
  calibrationMessage,
  formatScore,
  formatTrend,
  momentumGain,
  scoreNumber,
  topicsNeedingAttention,
} from "../lib/feedbackHierarchy";
import { revealElementByIdWhenSettled } from "../lib/hashFocus";

function TopicSummaryTable({
  topics,
  label = "Topic summary",
}: {
  topics: FeedbackTopicScore[];
  label?: string;
}) {
  return (
    <div className="table-scroll">
      <table className="feedback-summary-table" aria-label={label}>
        <thead>
          <tr>
            <th>Topic</th>
            <th>Best</th>
            <th>Latest</th>
            <th>Attempts</th>
            <th>Badge</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {topics.map((topic) => (
            <tr key={topic.topic_id}>
              <td>{topic.topic_name}</td>
              <td>{formatScore(topic.best_score_percent)}</td>
              <td>{topic.badge === "not_attempted" ? "—" : formatScore(topic.latest_score_percent)}</td>
              <td>{topic.attempt_count}</td>
              <td>
                <AttemptBadge
                  badge={topic.badge}
                  attemptCount={topic.attempt_count}
                  best={topic.best_score_percent}
                />
              </td>
              <td>{topicStatusLabel(topic)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
  const location = useLocation();
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

  useEffect(() => {
    if (!detail || location.hash !== "#needs-attention") return;
    const names = topicsNeedingAttention(detail);
    if (!names || names.length === 0) return;
    return revealElementByIdWhenSettled("needs-attention");
  }, [detail, location.hash]);

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
  const needingAttention = topicsNeedingAttention(detail) ?? [];
  const gain = momentumGain(detail.topics.map((topic) => topic.attempt_scores));

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
        <h2
          id="needs-attention"
          tabIndex={-1}
          className={needingAttention.length > 0 ? "progress-label" : "sr-only"}
        >
          {needingAttention.length > 0
            ? `Needs attention: ${needingAttention.join(", ")}`
            : "No topics need attention right now."}
        </h2>
        <Milestone mastered={detail.concepts_mastered} attempted={detail.concepts_attempted} />
        <SubjectEngagement rates={engagement} />
        <FocusLine focus={detail.focus} />
        <AchievementBanner tier={detail.achievement} />
        <div className="feedback-summary__text">
          <p>{detail.summary}</p>
        </div>
        {gain !== null && gain > 0 && (
          <p className="feedback-momentum">↑ up {gain}% since you started</p>
        )}
      </section>

      <div className="feedback-grid">
        {detail.topics.map((topic) => (
          <TopicScoreCard key={topic.topic_id} subjectId={subjectId} topic={topic} />
        ))}
      </div>

      <TopicSummaryTable topics={detail.topics} />

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
            <TopicSummaryTable topics={detail.topics} label="Report card topics" />
          </div>
        )}
      </div>
    </div>
  );
}
