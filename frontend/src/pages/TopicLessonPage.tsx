import { useState } from "react";
import { Link } from "react-router-dom";

import {
  AttemptHistoryList,
  AttemptHistoryTrigger,
  formatAttempt,
  formatAttemptWhen,
} from "../components/AttemptHistory";
import { Crumbs } from "../components/Crumbs";
import { MarkdownContent } from "../components/MarkdownContent";
import { latestRetakeLabel, passedBestLabel, quizActionLabel, trackedAttempts } from "../lib/quizAction";
import { useTopicLesson } from "../lib/useTopicLesson";

export function TopicLessonPage() {
  const {
    lesson,
    subjectName,
    topicTitle,
    quizSummary,
    error,
    subjectPath,
  } = useTopicLesson();
  const [showHistory, setShowHistory] = useState(false);

  const quizUnlocked = Boolean(lesson?.quiz_unlocked && lesson.quiz_id);
  const quizId = lesson?.quiz_id ?? quizSummary?.id ?? null;
  const attempts = trackedAttempts(quizSummary?.recent_attempts ?? []);
  const latestAttempt = attempts[0] ?? null;
  const quizCta = quizActionLabel(quizSummary);
  const latestWhen = latestAttempt ? formatAttemptWhen(latestAttempt) : null;
  const passedLabel = passedBestLabel(quizSummary);
  const retakeLabel = latestRetakeLabel(quizSummary);
  const markdownStartsWithHeading = /^#\s+/m.test(lesson?.markdown.trim() ?? "");

  return (
    <>
      {!lesson && !error && (
        <div className="center-state" role="status">
          Loading topic lesson…
        </div>
      )}

      {error && (
        <div className="center-state">
          <p className="form__error" role="alert">
            {error}
          </p>
          <div className="actions" style={{ justifyContent: "center" }}>
            <Link to={subjectPath} className="btn btn--soft btn--sm">
              Back to subject
            </Link>
          </div>
        </div>
      )}

      {lesson && (
        <div className="lesson-view topic-lesson-page">
          <Crumbs
            parts={[
              { label: "Subjects", to: "/" },
              { label: subjectName, to: subjectPath },
              { label: "Topic lesson" },
            ]}
          />
          <h1 className="sr-only">Topic lesson</h1>

          <div className="lesson-layout">
            <section className="lesson-layout__main" aria-label="Topic lesson">
              <article className="panel lesson-overview">
                {!markdownStartsWithHeading && (
                  <div className="lesson-overview__header">
                    <h2>{lesson.title || topicTitle}</h2>
                  </div>
                )}
                <div className="lesson-overview__scroll markdown">
                  <MarkdownContent>{lesson.markdown}</MarkdownContent>
                </div>
              </article>
            </section>

            <aside className="lesson-layout__aside" aria-label="Topic quiz" id="quiz">
              <div className="lesson-quiz-panel">
                <div className="lesson-quiz-panel__hero">
                  <div>
                    <h2 className="lesson-quiz-panel__title">Topic quiz</h2>
                    <div className="lesson-quiz-panel__meta">
                      {passedLabel ? (
                        <span className="badge badge--ok">{passedLabel}</span>
                      ) : quizUnlocked ? (
                        <span className="badge badge--info">Unlocked</span>
                      ) : (
                        <span className="badge badge--locked">Locked</span>
                      )}
                      <span className="lesson-toolbar__hint">
                        {quizSummary
                          ? `${quizSummary.attempt_count} attempt${quizSummary.attempt_count === 1 ? "" : "s"}`
                          : "No quiz linked"}
                      </span>
                    </div>
                  </div>
                  <div className="lesson-quiz-panel__actions">
                    {quizUnlocked && quizId ? (
                      <Link to={`/quizzes/${quizId}`} className="btn btn--sm">
                        {quizCta}
                      </Link>
                    ) : (
                      <button type="button" className="btn btn--sm" disabled>
                        Start quiz
                      </button>
                    )}
                    {!quizUnlocked && (
                      <p className="lesson-toolbar__hint">
                        Complete the topic lesson first.
                      </p>
                    )}
                  </div>
                </div>

                <div className="lesson-quiz-panel__latest">
                  <h3>Last attempt</h3>
                  {latestAttempt ? (
                    <div className="lesson-quiz-panel__latest-row">
                      <p>
                        <strong>{retakeLabel ?? formatAttempt(latestAttempt)}</strong>
                        {!retakeLabel && latestWhen && (
                          <span className="lesson-toolbar__hint"> · {latestWhen}</span>
                        )}
                        {!retakeLabel && quizSummary?.best_score_percent != null && (
                          <span className="lesson-toolbar__hint">
                            {" "}
                            · Best {Math.round(Number(quizSummary.best_score_percent))}%
                          </span>
                        )}
                      </p>
                      <Link to={`/attempts/${latestAttempt.id}`} className="btn btn--soft btn--sm">
                        View attempt
                      </Link>
                    </div>
                  ) : (
                    <p className="lesson-toolbar__hint">No attempts yet.</p>
                  )}
                </div>

                <AttemptHistoryTrigger
                  title="Show full history"
                  attempts={attempts}
                  active={showHistory}
                  onOpen={() => setShowHistory(true)}
                />
                {showHistory && <AttemptHistoryList attempts={attempts} />}
              </div>
            </aside>
          </div>
        </div>
      )}
    </>
  );
}
