import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

import { getFeedbackHighlights, getFeedbackOverview } from "../api/feedback";
import { fetchLearningDirectory } from "../api/materials";
import type { FeedbackOverview, ReviewNudgeOut } from "../api/types";
import { ApiError } from "../api/types";
import { RatingBadge } from "../components/BadgePill";
import { Crumbs } from "../components/Crumbs";
import {
  DueReviewsTable,
  FeedbackEntriesTable,
  type DueReviewRow,
} from "../components/FeedbackEntriesTable";
import { formatScore, scoreNumber } from "../lib/feedbackHierarchy";
import { revealElementByIdWhenSettled } from "../lib/hashFocus";

type ReviewTarget = { subjectId: string; subjectName: string };

export function FeedbackSubjectPickerPage() {
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [overview, setOverview] = useState<FeedbackOverview | null>(null);
  const [dueReviews, setDueReviews] = useState<ReviewNudgeOut[]>([]);
  const [reviewTargets, setReviewTargets] = useState<Map<string, ReviewTarget>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const allowDueFocus = useRef(true);
  const [dueLoaded, setDueLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDueLoaded(false);
    void getFeedbackOverview()
      .then((data) => {
        if (!cancelled) {
          setOverview(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load feedback.");
        }
      });
    void Promise.all([getFeedbackHighlights(), fetchLearningDirectory()])
      .then(([highlights, directory]) => {
        if (cancelled) return;
        const map = new Map<string, ReviewTarget>();
        for (const subject of directory.subjects) {
          for (const topic of subject.topics) {
            for (const subtopic of topic.subtopics) {
              map.set(subtopic.id, { subjectId: subject.id, subjectName: subject.name });
            }
          }
        }
        setReviewTargets(map);
        setDueReviews(highlights.due_reviews);
      })
      .catch(() => {
        if (!cancelled) {
          setDueReviews([]);
          setReviewTargets(new Map());
        }
      })
      .finally(() => {
        if (!cancelled) setDueLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const dueRows = useMemo<DueReviewRow[]>(
    () =>
      dueReviews.map((nudge) => {
        const target = reviewTargets.get(nudge.subtopic_id);
        return {
          subtopicId: nudge.subtopic_id,
          subtopicName: nudge.subtopic_name,
          topicName: nudge.topic_name,
          daysSinceLastAttempt: nudge.days_since_last_attempt,
          dueIntervalDays: nudge.due_interval_days,
          subjectId: target?.subjectId ?? null,
          subjectName: target?.subjectName ?? null,
          href: target ? `/subjects/${target.subjectId}/feedback` : null,
        };
      }),
    [dueReviews, reviewTargets],
  );
  const subjectFilter = searchParams.get("subject");
  const visibleDue = subjectFilter
    ? dueRows.filter((row) => row.subjectId === subjectFilter)
    : dueRows;
  const showDue = visibleDue.length > 0 || searchParams.get("section") === "due";

  useEffect(() => {
    const wantsDue = location.hash === "#feedback-due" || searchParams.get("section") === "due";
    if (!wantsDue || !overview || !dueLoaded || !allowDueFocus.current) return;
    return revealElementByIdWhenSettled("feedback-due");
  }, [dueLoaded, location.hash, overview, searchParams, showDue]);

  const attempted = overview?.subjects.filter((subject) => scoreNumber(subject.score_percent) !== null).length ?? 0;
  const quizzes =
    overview?.subjects.reduce((sum, subject) => sum + subject.quizzes_taken, 0) ?? 0;
  const mastered =
    overview?.subjects.reduce((sum, subject) => sum + subject.concepts_mastered, 0) ?? 0;
  const concepts =
    overview?.subjects.reduce((sum, subject) => sum + subject.concepts_attempted, 0) ?? 0;

  return (
    <div className="feedback-view">
      <Crumbs parts={[{ label: "Feedback" }]} />
      <header className="page-head">
        <h1>Feedback</h1>
        <p>Topic by topic, then the subject, then every subject together.</p>
      </header>

      {error && (
        <div className="alert alert--warning" role="alert">
          <div className="alert__row">
            <p>{error}</p>
            <button
              type="button"
              className="btn btn--sm btn--outline"
              onClick={() => {
                allowDueFocus.current = false;
                setError(null);
                setAttempt((value) => value + 1);
              }}
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {!overview && !error && (
        <div className="panel panel--static" role="status" aria-live="polite" aria-busy="true">
          <p className="muted">Loading feedback…</p>
        </div>
      )}

      {overview && (
        <>
          <section className="feedback-summary" aria-label="Across all subjects">
            <p className="feedback-summary__kicker">Across all subjects</p>
            <div className="stat-kpis">
              <article className="stat-kpi">
                <p className="kicker">Average score</p>
                <p className="stat-kpi__value">{formatScore(overview.score_percent)}</p>
                {scoreNumber(overview.score_percent) !== null && (
                  <RatingBadge value={overview.score_percent} />
                )}
                <p>Average of each subject you have attempted. A subject with no topic quiz yet is left out.</p>
              </article>
              <article className="stat-kpi">
                <p className="kicker">Subjects attempted</p>
                <p className="stat-kpi__value">
                  {attempted}
                  <span className="stat-kpi__of">/{overview.subjects.length}</span>
                </p>
                <p>Subjects with at least one scored topic quiz</p>
              </article>
              <article className="stat-kpi">
                <p className="kicker">Topic quizzes</p>
                <p className="stat-kpi__value">{quizzes}</p>
                <p>Quizzes taken across every subject</p>
              </article>
              <article className="stat-kpi">
                <p className="kicker">Concepts mastered</p>
                <p className="stat-kpi__value">
                  {concepts === 0 ? "—" : mastered}
                  {concepts > 0 && <span className="stat-kpi__of">/{concepts}</span>}
                </p>
                <p>
                  {concepts === 0
                    ? "Concepts mastered out of those you've tried"
                    : `${mastered} mastered out of ${concepts} you've tried`}
                </p>
              </article>
            </div>
          </section>

          <section className="panel panel--static catalog-panel" aria-labelledby="feedback-entries-heading">
            <header className="catalog-panel__head">
              <h2 id="feedback-entries-heading">Feedback by subject</h2>
              <p>Score, status, trend, and the topics to revisit for each subject.</p>
            </header>
            {overview.subjects.length === 0 ? (
              <div className="alert alert--info" role="status">
                No enrolled subjects yet.
              </div>
            ) : (
              <FeedbackEntriesTable subjects={overview.subjects} />
            )}
          </section>

          {showDue && (
            <section
              id="feedback-due"
              className="panel panel--static catalog-panel"
              aria-labelledby="feedback-due-heading"
            >
              <header className="catalog-panel__head">
                <h2 id="feedback-due-heading" className="section-title" tabIndex={-1}>
                  Due for review
                </h2>
                <p>
                  {visibleDue.length === 0
                    ? "No subtopics are due for review in this subject."
                    : `${visibleDue.length} ${visibleDue.length === 1 ? "subtopic is" : "subtopics are"} past the review interval.`}
                </p>
              </header>
              {visibleDue.length > 0 ? <DueReviewsTable rows={visibleDue} /> : null}
            </section>
          )}
        </>
      )}
    </div>
  );
}
