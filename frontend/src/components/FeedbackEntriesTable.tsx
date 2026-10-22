import { Link } from "react-router-dom";

import type { FeedbackSubjectCard, FeedbackTrend } from "../api/types";
import type { StatusKind } from "../lib/badges";
import { achievementCopy, formatTrend, scoreNumber } from "../lib/feedbackHierarchy";
import { EmptyValue, ScoreBadge, StatusBadge, SubjectBadge } from "./BadgePill";
import { FocusLine } from "./FeedbackExtras";

function entryStatus(subject: FeedbackSubjectCard): StatusKind {
  if (subject.achievement) return subject.achievement;
  if (subject.quizzes_taken === 0 || scoreNumber(subject.score_percent) === null) {
    return "not_attempted";
  }
  return "in_progress";
}

function trendStatus(trend: Exclude<FeedbackTrend, null>): StatusKind {
  switch (trend) {
    case "improving":
      return "improving";
    case "declining":
      return "declining";
    case "flat":
      return "flat";
    default: {
      const unreachable: never = trend;
      return unreachable;
    }
  }
}

export type DueReviewRow = {
  subtopicId: string;
  subtopicName: string;
  topicName: string;
  daysSinceLastAttempt: number;
  dueIntervalDays: number;
  subjectId: string | null;
  subjectName: string | null;
  href: string | null;
};

export function FeedbackEntriesTable({ subjects }: { subjects: FeedbackSubjectCard[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table data-table--cards catalog-table">
        <caption className="sr-only">Feedback by subject</caption>
        <thead>
          <tr>
            <th scope="col">Subject</th>
            <th scope="col">Score</th>
            <th scope="col">Status</th>
            <th scope="col">Quizzes</th>
            <th scope="col">Trend</th>
            <th scope="col">Concepts</th>
            <th scope="col">Highlights</th>
          </tr>
        </thead>
        <tbody>
          {subjects.map((subject) => {
            const trend = formatTrend(subject.trend, subject.trend_delta);
            const achievement = subject.achievement ? achievementCopy(subject.achievement) : null;
            return (
              <tr key={subject.subject_id}>
                <td data-label="Subject" className="catalog-wrap">
                  <div className="catalog-subject">
                    <Link
                      to={`/subjects/${subject.subject_id}/feedback`}
                      className="catalog-subject__link"
                    >
                      <SubjectBadge name={subject.subject_name} />
                    </Link>
                    <span className="catalog-meta">{subject.grade_name}</span>
                    <Link
                      to={`/subjects/${subject.subject_id}/feedback`}
                      className="btn btn--sm btn--outline"
                    >
                      View feedback
                    </Link>
                  </div>
                </td>
                <td data-label="Score">
                  <ScoreBadge value={subject.score_percent} />
                </td>
                <td data-label="Status" className="catalog-wrap">
                  <span className="catalog-stack">
                    <StatusBadge status={entryStatus(subject)} />
                    {achievement ? <span className="catalog-meta">{achievement.detail}</span> : null}
                  </span>
                </td>
                <td data-label="Quizzes">
                  {subject.quizzes_taken === 0
                    ? "No topic quizzes yet"
                    : `${subject.quizzes_taken} topic ${subject.quizzes_taken === 1 ? "quiz" : "quizzes"}`}
                </td>
                <td data-label="Trend" className="catalog-wrap">
                  {subject.trend && trend ? (
                    <span className="catalog-stack">
                      <StatusBadge status={trendStatus(subject.trend)} />
                      <span className="catalog-meta">{trend}</span>
                    </span>
                  ) : (
                    <EmptyValue />
                  )}
                </td>
                <td data-label="Concepts">
                  {subject.concepts_attempted === 0 ? (
                    <EmptyValue />
                  ) : (
                    `${subject.concepts_mastered} of ${subject.concepts_attempted} mastered`
                  )}
                </td>
                <td data-label="Highlights" className="catalog-wrap">
                  <div className="catalog-stack catalog-stack--rows">
                    {subject.strongest_topic ? (
                      <span>
                        Strongest topic: {subject.strongest_topic.topic_name}{" "}
                        <ScoreBadge value={subject.strongest_topic.best_score_percent} />
                      </span>
                    ) : null}
                    {subject.weakest_topic ? (
                      <span>
                        Weakest topic: {subject.weakest_topic.topic_name}{" "}
                        <ScoreBadge value={subject.weakest_topic.best_score_percent} />
                      </span>
                    ) : null}
                    <FocusLine focus={subject.focus} />
                    {!subject.strongest_topic && !subject.weakest_topic && !subject.focus ? (
                      <EmptyValue />
                    ) : null}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function DueReviewsTable({ rows }: { rows: DueReviewRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="table-scroll">
      <table className="data-table data-table--cards catalog-table">
        <caption className="sr-only">Due for review</caption>
        <thead>
          <tr>
            <th scope="col">Subtopic</th>
            <th scope="col">Topic</th>
            <th scope="col">Subject</th>
            <th scope="col">Last attempt</th>
            <th scope="col">Interval</th>
            <th scope="col">Review</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.subtopicId}>
              <td data-label="Subtopic">{row.subtopicName}</td>
              <td data-label="Topic">{row.topicName}</td>
              <td data-label="Subject">
                {row.subjectName ? <SubjectBadge name={row.subjectName} /> : <EmptyValue />}
              </td>
              <td data-label="Last attempt" className="catalog-wrap">
                <span className="catalog-stack">
                  <StatusBadge status="due" />
                  <span className="catalog-meta">
                    Last attempted {row.daysSinceLastAttempt}{" "}
                    {row.daysSinceLastAttempt === 1 ? "day" : "days"} ago
                  </span>
                </span>
              </td>
              <td data-label="Interval">Every {row.dueIntervalDays} days</td>
              <td data-label="Review">
                {row.href ? (
                  <Link to={row.href} className="btn btn--sm btn--outline">
                    Review
                  </Link>
                ) : (
                  <EmptyValue />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
