import { Link } from "react-router-dom";

import type { SubjectStats } from "../lib/subjectStats";
import { BadgePill, EmptyValue, ScoreBadge, StatusBadge, SubjectBadge } from "./BadgePill";

function Count({ done, total, noun }: { done: number; total: number; noun: string }) {
  if (total === 0) return <EmptyValue />;
  return (
    <span>
      {done} of {total} {noun}
    </span>
  );
}

export function SubjectStatsTable({ rows }: { rows: SubjectStats[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table data-table--cards catalog-table">
        <caption className="sr-only">Subject progress</caption>
        <thead>
          <tr>
            <th scope="col">Subject</th>
            <th scope="col">Status</th>
            <th scope="col">Topics</th>
            <th scope="col">Progress</th>
            <th scope="col">Lessons</th>
            <th scope="col">Quizzes</th>
            <th scope="col">Best score</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td data-label="Subject" className="catalog-wrap">
                <div className="catalog-subject">
                  <span className="catalog-subject__name">
                    {row.href ? (
                      <Link to={row.href} className="catalog-subject__link">
                        <SubjectBadge name={row.name} />
                      </Link>
                    ) : (
                      <SubjectBadge name={row.name} tone="locked" />
                    )}
                    {row.code ? <BadgePill>{row.code}</BadgePill> : null}
                  </span>
                  <span className="catalog-meta">
                    {row.gradeName} · {row.periodName}
                  </span>
                  {row.href ? (
                    <Link to={row.href} className="btn btn--sm btn--outline" aria-label={`Open ${row.name}`}>
                      Open
                    </Link>
                  ) : null}
                </div>
              </td>
              <td data-label="Status">
                <StatusBadge status={row.status} />
              </td>
              <td data-label="Topics">
                <Count done={row.topicsDone} total={row.topicsTotal} noun="complete" />
              </td>
              <td data-label="Progress">
                <div className="catalog-progress">
                  <span className="catalog-meta">{row.progressPercent}%</span>
                  <div
                    className={`progress ${
                      row.progressPercent >= 100
                        ? "progress--complete"
                        : row.progressPercent > 0
                          ? "progress--in-progress"
                          : ""
                    }`}
                    role="progressbar"
                    aria-valuenow={row.progressPercent}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${row.name} average topic progress`}
                  >
                    <span style={{ width: `${row.progressPercent}%` }} />
                  </div>
                </div>
              </td>
              <td data-label="Lessons">
                <Count done={row.lessonsDone} total={row.lessonsTotal} noun="done" />
              </td>
              <td data-label="Quizzes" className="catalog-wrap">
                {row.quizzesAvailable === 0 ? (
                  <EmptyValue />
                ) : (
                  <span className="catalog-stack">
                    <span>
                      {row.quizzesPassed} of {row.quizzesAvailable} passed
                    </span>
                    <span className="catalog-meta">
                      {row.attemptCount} {row.attemptCount === 1 ? "attempt" : "attempts"}
                    </span>
                  </span>
                )}
              </td>
              <td data-label="Best score">
                <ScoreBadge value={row.bestScorePercent} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
