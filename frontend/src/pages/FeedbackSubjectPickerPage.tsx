import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { getFeedbackHighlights } from "../api/feedback";
import { fetchLearningDirectory } from "../api/materials";
import type { FeedbackHighlights, LearningDirectory } from "../api/types";
import { ApiError } from "../api/types";
import { Crumbs } from "../components/Crumbs";

function pct(value: string | number | null): number | null {
  if (value === null || value === undefined) return null;
  return typeof value === "string" ? Number(value) : value;
}

export function FeedbackSubjectPickerPage() {
  const [directory, setDirectory] = useState<LearningDirectory | null>(null);
  const [highlights, setHighlights] = useState<FeedbackHighlights | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [directoryData, highlightsData] = await Promise.all([
          fetchLearningDirectory(),
          getFeedbackHighlights(),
        ]);
        if (!cancelled) {
          setDirectory(directoryData);
          setHighlights(highlightsData);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load subjects.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const subjects = directory?.subjects ?? [];

  const subjectIdBySubtopicId = useMemo(() => {
    const map = new Map<string, string>();
    for (const subject of subjects) {
      for (const topic of subject.topics) {
        for (const subtopic of topic.subtopics) {
          map.set(subtopic.id, subject.id);
        }
      }
    }
    return map;
  }, [subjects]);

  const dueReviews = highlights?.due_reviews ?? [];
  const trajectories = highlights?.trajectories ?? [];

  return (
    <>
      <Crumbs parts={[{ label: "Feedback" }]} />
      <header className="page-head">
        <h1>Feedback</h1>
        <p>Pick a subject to see your personalized quiz feedback.</p>
      </header>

      {error && (
        <p className="form__error" role="alert">
          {error}
        </p>
      )}

      {!directory && !error && (
        <p className="muted" role="status">
          Loading subjects…
        </p>
      )}

      {dueReviews.length > 0 && (
        <>
          <h2 className="section-title">Due for review</h2>
          <div className="answer-review" style={{ marginBottom: "1rem" }}>
            {dueReviews.map((nudge) => {
              const subjectId = subjectIdBySubtopicId.get(nudge.subtopic_id);
              return (
                <div key={nudge.subtopic_id} className="answer-row">
                  <span className="answer-row__num">{nudge.subtopic_name}</span>
                  <span>
                    Last attempted {nudge.days_since_last_attempt} days ago · due after{" "}
                    {nudge.due_interval_days} days
                  </span>
                  {subjectId && (
                    <Link to={`/subjects/${subjectId}/feedback`} className="btn btn--sm">
                      Review
                    </Link>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {trajectories.length > 0 && (
        <>
          <h2 className="section-title">Trends</h2>
          <div className="answer-review" style={{ marginBottom: "1rem" }}>
            {trajectories.map((trajectory) => {
              const change = pct(trajectory.recent_change);
              const status =
                trajectory.trend === "improving"
                  ? "is-correct"
                  : trajectory.trend === "declining"
                    ? "is-wrong"
                    : "";
              const subjectId = subjectIdBySubtopicId.get(trajectory.subtopic_id);
              return (
                <div key={trajectory.subtopic_id} className={`answer-row ${status}`}>
                  <span className="answer-row__num">{trajectory.subtopic_name}</span>
                  <span>
                    {trajectory.trend}
                    {change !== null && ` · ${change >= 0 ? "+" : ""}${Math.round(change)}%`}
                  </span>
                  {subjectId && (
                    <Link to={`/subjects/${subjectId}/feedback`} className="btn btn--sm">
                      View
                    </Link>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {directory && subjects.length === 0 && (
        <div className="alert alert--info">No enrolled subjects yet.</div>
      )}

      {subjects.length > 0 && (
        <div className="grid grid--2">
          {subjects.map((subject) => (
            <Link key={subject.id} to={`/subjects/${subject.id}/feedback`} className="card">
              <h2>{subject.name}</h2>
              <p>
                {subject.grade_name} · {subject.academic_period_name}
              </p>
              <div className="meta-row">
                <span className="badge badge--info">View feedback</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
