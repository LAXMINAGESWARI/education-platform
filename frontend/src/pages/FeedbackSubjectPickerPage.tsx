import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getFeedbackOverview } from "../api/feedback";
import type { FeedbackOverview, FeedbackSubjectCard } from "../api/types";
import { ApiError } from "../api/types";
import { Crumbs } from "../components/Crumbs";
import { formatScore, formatTrend } from "../lib/feedbackHierarchy";

function SubjectCard({ subject }: { subject: FeedbackSubjectCard }) {
  const trend = formatTrend(subject.trend, subject.trend_delta);
  return (
    <Link to={`/subjects/${subject.subject_id}/feedback`} className="card feedback-card">
      <p className="kicker">{subject.grade_name}</p>
      <h2>{subject.subject_name}</h2>
      <p className="feedback-card__score">{formatScore(subject.score_percent)}</p>
      <p className="progress-label">
        {subject.quizzes_taken === 0
          ? "No topic quizzes yet"
          : `${subject.quizzes_taken} topic ${subject.quizzes_taken === 1 ? "quiz" : "quizzes"} taken`}
      </p>
      {trend && <p className="feedback-card__trend">{trend}</p>}
      {subject.strongest_topic && (
        <p className="progress-label">
          Strongest topic: {subject.strongest_topic.topic_name} (
          {formatScore(subject.strongest_topic.best_score_percent)})
        </p>
      )}
      {subject.weakest_topic && (
        <p className="progress-label">
          Weakest topic: {subject.weakest_topic.topic_name} (
          {formatScore(subject.weakest_topic.best_score_percent)})
        </p>
      )}
    </Link>
  );
}

export function FeedbackSubjectPickerPage() {
  const [overview, setOverview] = useState<FeedbackOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getFeedbackOverview()
      .then((data) => {
        if (!cancelled) setOverview(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load feedback.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="feedback-view">
      <Crumbs parts={[{ label: "Feedback" }]} />
      <header className="page-head">
        <h1>Feedback</h1>
        <p>Topic by topic, then the subject, then every subject together.</p>
      </header>

      {error && (
        <p className="form__error" role="alert">
          {error}
        </p>
      )}

      {!overview && !error && (
        <p className="muted" role="status">
          Loading feedback…
        </p>
      )}

      {overview && (
        <>
          <section className="feedback-summary" aria-label="Across all subjects">
            <p className="feedback-summary__kicker">Across all subjects</p>
            <p className="feedback-card__score">{formatScore(overview.score_percent)}</p>
            <p className="progress-label">
              Average of each subject you have attempted. A subject with no topic quiz yet is left out.
            </p>
          </section>
          {overview.subjects.length === 0 ? (
            <div className="alert alert--info">No enrolled subjects yet.</div>
          ) : (
            <div className="feedback-grid">
              {overview.subjects.map((subject) => (
                <SubjectCard key={subject.subject_id} subject={subject} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
