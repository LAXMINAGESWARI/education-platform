import { Link } from "react-router-dom";

import type { SubjectNode } from "../api/types";
import { subjectBlurb, subjectCardCounts, subjectProgress, subjectStudyStatus } from "../lib/subjectMaterial";
import { BadgePill, StatusBadge } from "./BadgePill";
import { Crumbs } from "./Crumbs";
import { SchoolMaterialPanel } from "./SchoolMaterialPanel";

function SubjectRouteBody({
  kind,
  message,
}: {
  kind: "loading" | "error" | "missing";
  message?: string;
}) {
  switch (kind) {
    case "loading":
      return (
        <div className="panel panel--static" role="status" aria-live="polite" aria-busy="true">
          <p className="muted">Loading subject…</p>
        </div>
      );
    case "error":
      return (
        <p className="form__error" role="alert">
          {message ?? "Could not load this subject."}
        </p>
      );
    case "missing":
      return (
        <div className="subject-status">
          <div className="alert alert--info" role="status">
            This subject is not on your learning directory.
          </div>
          <Link className="btn btn--sm btn--outline" to="/">
            Back to subjects
          </Link>
        </div>
      );
    default: {
      const unreachable: never = kind;
      return unreachable;
    }
  }
}

export function SubjectRouteStatus({
  kind,
  message,
}: {
  kind: "loading" | "error" | "missing";
  message?: string;
}) {
  return (
    <div className="subject-view">
      <Crumbs
        parts={[
          { label: "Subjects", to: "/" },
          { label: "Subject" },
        ]}
      />
      <SubjectRouteBody kind={kind} message={message} />
    </div>
  );
}

export function SubjectDetail({ subject }: { subject: SubjectNode }) {
  const topics = subject.topics;
  const { done, total, pct } = subjectProgress(subject);
  const status = subjectStudyStatus(subject);
  const counts = subjectCardCounts(topics);
  const placement = `${subject.grade_name} · ${subject.academic_period_name}`;
  const blurb = subjectBlurb(subject);
  const complete = total > 0 && pct >= 100;

  return (
    <div className="subject-view">
      <header className="subject-view__head">
        <Crumbs
          parts={[
            { label: "Subjects", to: "/" },
            { label: subject.name },
          ]}
        />
        <article className="card subject-summary" aria-labelledby="subject-detail-heading">
          <p className="kicker">School material</p>
          <h1 id="subject-detail-heading">{subject.name}</h1>
          <p className="subject-summary__meta">{placement}</p>
          {blurb !== placement ? <p>{blurb}</p> : null}
          {topics.length === 0 ? (
            <>
              <div className="progress-label">Coming soon</div>
              <ul className="meta-row subject-summary__facts">
                <li>
                  <span className="badge badge--locked">Locked</span>
                </li>
              </ul>
              <div className="alert alert--info" role="status">
                No school material published yet.
              </div>
            </>
          ) : (
            <>
              <div className="progress-label" id="subject-progress-label">
                {done}/{total} topics complete · {pct}%
              </div>
              <div
                className={`progress ${complete ? "progress--complete" : "progress--in-progress"}`}
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={pct}
                aria-label={`Subject completion, ${done} of ${total} topics, ${pct} percent`}
              >
                <span style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
              </div>
              <ul className="meta-row subject-summary__facts">
                <li>
                  <StatusBadge status={status} />
                </li>
                {counts.lessonsTotal > 0 ? (
                  <li>
                    <BadgePill>
                      {counts.lessonsDone} of {counts.lessonsTotal} lessons finished
                    </BadgePill>
                  </li>
                ) : null}
                {counts.quizzesAvailable > 0 ? (
                  <li>
                    <BadgePill>
                      {counts.quizzesPassed} of {counts.quizzesAvailable} quizzes passed
                    </BadgePill>
                  </li>
                ) : null}
              </ul>
            </>
          )}
        </article>
      </header>
      {topics.length > 0 ? (
        <div className="material-pane material-pane--school">
          <SchoolMaterialPanel subjectId={subject.id} topics={topics} />
        </div>
      ) : null}
    </div>
  );
}
