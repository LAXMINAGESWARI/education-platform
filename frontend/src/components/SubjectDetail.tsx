import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getFeedbackHighlights, getFeedbackSubject } from "../api/feedback";
import type { SubjectNode } from "../api/types";
import { dueReviewsPath, dueTopicIds, subjectFeedbackPath, weakTopicsPath } from "../lib/feedbackLinks";
import { topicsNeedingAttention, weakTopicNames } from "../lib/feedbackHierarchy";
import {
  subjectBlurb,
  subjectCardCounts,
  subjectProgress,
  subjectResumeTarget,
  subjectStudyStatus,
} from "../lib/subjectMaterial";
import { BadgePill, StatusBadge } from "./BadgePill";
import { Crumbs } from "./Crumbs";
import { SchoolMaterialPanel } from "./SchoolMaterialPanel";

function SubjectRouteBody({
  kind,
  message,
  onRetry,
}: {
  kind: "loading" | "error" | "missing";
  message?: string;
  onRetry?: () => void;
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
        <div className="subject-status">
          <p className="form__error" role="alert">
            {message ?? "Could not load this subject."}
          </p>
          <button type="button" className="btn btn--sm" onClick={() => onRetry?.()}>
            Try again
          </button>
          <Link className="btn btn--sm btn--outline" to="/">
            Back to subjects
          </Link>
        </div>
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
  onRetry,
}: {
  kind: "loading" | "error" | "missing";
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="subject-view">
      <Crumbs
        parts={[
          { label: "Subjects", to: "/" },
          { label: "Subject" },
        ]}
      />
      <SubjectRouteBody kind={kind} message={message} onRetry={onRetry} />
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
  const resume = subjectResumeTarget(subject);
  const [needingAttention, setNeedingAttention] = useState<readonly string[]>([]);
  const [dueTopics, setDueTopics] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    setNeedingAttention((current) => (current.length === 0 ? current : []));
    const apply = (next: readonly string[]) => {
      if (cancelled) return;
      setNeedingAttention((current) =>
        current.length === next.length && current.every((name, index) => name === next[index])
          ? current
          : next,
      );
    };
    void Promise.resolve()
      .then(() => getFeedbackSubject(subject.id))
      .then((detail) => {
        const fromApi = topicsNeedingAttention(detail);
        apply(fromApi ?? weakTopicNames(subject.topics));
      })
      .catch(() => {
        apply(weakTopicNames(subject.topics));
      });
    return () => {
      cancelled = true;
    };
  }, [subject]);

  useEffect(() => {
    if (subject.topics.length === 0) return;
    let cancelled = false;
    void Promise.resolve(getFeedbackHighlights())
      .then((highlights) => {
        if (cancelled || !highlights) return;
        const next = dueTopicIds(subject.topics, highlights.due_reviews);
        setDueTopics((current) =>
          current.length === next.length && current.every((id, index) => id === next[index])
            ? current
            : next,
        );
      })
      .catch(() => {
        if (!cancelled) setDueTopics([]);
      });
    return () => {
      cancelled = true;
    };
  }, [subject]);

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
              <div className="progress-label">
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
              <nav className="subject-shortcuts" aria-label="Subject shortcuts">
                {resume ? (
                  <Link className="pill-btn" to={resume.href}>
                    <span className="pill-btn__face">
                      <span className="pill-btn__action">Resume where you stopped</span>
                      <span className="pill-btn__detail">
                        {resume.topicTitle}
                        {resume.slide != null ? ` · slide ${resume.slide}` : ""}
                      </span>
                    </span>
                  </Link>
                ) : null}
                <Link className="pill-btn" to={subjectFeedbackPath(subject.id)}>
                  <span className="pill-btn__face">
                    <span className="pill-btn__action">Subject feedback</span>
                  </span>
                </Link>
                <Link className="pill-btn" to={dueReviewsPath(subject.id)}>
                  <span className="pill-btn__face">
                    <span className="pill-btn__action">Due reviews</span>
                  </span>
                </Link>
                {needingAttention.length > 0 ? (
                  <Link className="pill-btn" to={weakTopicsPath(subject.id)}>
                    <span className="pill-btn__face">
                      <span className="pill-btn__action">Weak topics</span>
                    </span>
                  </Link>
                ) : null}
              </nav>
            </>
          )}
        </article>
      </header>
      {topics.length > 0 ? (
        <div className="material-pane material-pane--school">
          <SchoolMaterialPanel subjectId={subject.id} topics={topics} dueTopicIds={dueTopics} />
        </div>
      ) : null}
    </div>
  );
}
