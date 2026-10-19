import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchLearningDirectory } from "../api/materials";
import type { QuizSummary } from "../api/types";
import { ApiError } from "../api/types";
import { AttemptHistoryList } from "../components/AttemptHistory";
import { Crumbs } from "../components/Crumbs";
import { QuizPerformanceChart } from "../components/QuizPerformanceChart";
import type { Crumb } from "../lib/crumbTrail";
import { quizActionLabel, trackedAttempts } from "../lib/quizAction";
import { findSubjectTopic, topicPagePath } from "../lib/subjectMaterial";
import { useSubtopicLesson } from "../lib/useSubtopicLesson";

function QuizHistoryView({
  pending,
  error,
  visible,
  crumbs,
  quizSummary,
  quizUnlocked,
  quizId,
}: {
  pending: boolean;
  error: string | null;
  visible: boolean;
  crumbs: Crumb[];
  quizSummary: QuizSummary | null;
  quizUnlocked: boolean;
  quizId: string | null;
}) {
  const attempts = trackedAttempts(quizSummary?.recent_attempts ?? []);
  const quizCta = quizActionLabel(quizSummary);
  const passThreshold = quizSummary?.pass_threshold_percent ?? 70;

  return (
    <>
      {pending && (
        <div className="center-state" role="status">
          Loading quiz history…
        </div>
      )}

      {error && (
        <div className="center-state">
          <p className="form__error" role="alert">
            {error}
          </p>
        </div>
      )}

      {visible && (
        <div className="lesson-view">
          <Crumbs parts={crumbs} />

          <div className="quiz-history-page">
            <div className="lesson-quiz-panel">
              <div className="lesson-quiz-panel__hero">
                <div>
                  <h2 className="lesson-quiz-panel__title">Quiz history</h2>
                  <div className="lesson-quiz-panel__meta">
                    {quizSummary?.passed ? (
                      <span className="badge badge--ok">Passed</span>
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
                </div>
              </div>

              <QuizPerformanceChart attempts={attempts} passThreshold={passThreshold} />

              <div className="lesson-quiz-panel__history">
                <h3>All attempts</h3>
                <AttemptHistoryList attempts={attempts} />
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function SubtopicQuizHistory() {
  const {
    lesson,
    subjectName,
    subtopicTitle,
    quizSummary,
    error,
    subjectPath,
    lessonPath,
  } = useSubtopicLesson();

  return (
    <QuizHistoryView
      pending={!lesson && !error}
      error={error}
      visible={Boolean(lesson)}
      crumbs={[
        { label: "Subjects", to: "/" },
        { label: subjectName, to: subjectPath },
        { label: subtopicTitle, to: lessonPath },
        { label: "Quiz history" },
      ]}
      quizSummary={quizSummary}
      quizUnlocked={Boolean(lesson?.quiz_unlocked && lesson.quiz_id)}
      quizId={lesson?.quiz_id ?? quizSummary?.id ?? null}
    />
  );
}

function TopicQuizHistory() {
  const { subjectId = "", topicId = "" } = useParams();
  const [subjectName, setSubjectName] = useState("Subject");
  const [topicTitle, setTopicTitle] = useState("Topic");
  const [quizSummary, setQuizSummary] = useState<QuizSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setReady(false);
    setError(null);
    void fetchLearningDirectory()
      .then((directory) => {
        if (cancelled) return;
        const found = findSubjectTopic(directory, subjectId, topicId);
        if (!found) {
          setError("This topic is not in your subjects.");
          return;
        }
        setSubjectName(found.subject.name);
        setTopicTitle(found.topic.title);
        setQuizSummary(found.topic.overall_quiz);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load quiz history.");
        }
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [subjectId, topicId]);

  const quizId = quizSummary?.id ?? null;

  return (
    <QuizHistoryView
      pending={!ready}
      error={error}
      visible={ready && !error}
      crumbs={[
        { label: "Subjects", to: "/" },
        { label: subjectName, to: `/subjects/${subjectId}` },
        { label: topicTitle, to: topicPagePath(subjectId, topicId) },
        { label: "Quiz history" },
      ]}
      quizSummary={quizSummary}
      quizUnlocked={Boolean(quizSummary?.unlocked && quizId)}
      quizId={quizId}
    />
  );
}

/** Subtopic lesson history, or the topic quiz when the route has a topic id. */
export function QuizHistoryPage() {
  const { topicId, subtopicId } = useParams();
  if (topicId && !subtopicId) return <TopicQuizHistory />;
  return <SubtopicQuizHistory />;
}
