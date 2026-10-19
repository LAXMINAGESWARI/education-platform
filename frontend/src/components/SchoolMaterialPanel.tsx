import { Link } from "react-router-dom";

import type { AttemptHistoryItem, MaterialProgress, QuizSummary, TopicNode } from "../api/types";
import { latestRetakeLabel, passedBestLabel, trackedAttempts } from "../lib/quizAction";
import { topicLessonPath } from "../lib/subjectMaterial";
import { formatAttemptWhen } from "./AttemptHistory";

function lessonStarted(progress: MaterialProgress | null | undefined, progressPercent: number): boolean {
  return progress?.status === "opened" || progress?.status === "completed" || progressPercent > 0;
}

function unitStatus(
  lessonDone: boolean,
  quiz: QuizSummary | null,
  progress: MaterialProgress | null | undefined,
  progressPercent: number,
): {
  label: string;
  cls: string;
} {
  if (lessonDone && quiz?.unlocked) return { label: "Quiz unlocked", cls: "badge--info" };
  if (lessonDone) return { label: "Lesson done", cls: "badge--info" };
  if (lessonStarted(progress, progressPercent)) return { label: "In progress", cls: "badge--warn" };
  return { label: "Not started", cls: "badge--locked" };
}

function QuizAttemptResult({ attempt }: { attempt: AttemptHistoryItem }) {
  const score =
    attempt.score_percent == null ? null : `${Math.round(Number(attempt.score_percent))}%`;

  if (attempt.status === "in_progress" || attempt.status === "abandoned") {
    return <span className="quiz-meta">Not finished</span>;
  }
  if (attempt.passed === true) {
    return (
      <span className="quiz-meta quiz-meta--ok">
        Passed{score ? ` · ${score}` : ""}
      </span>
    );
  }
  if (attempt.passed === false) {
    return (
      <span className="quiz-meta quiz-meta--fail">
        Not passed{score ? ` · ${score}` : ""}
      </span>
    );
  }
  return <span className="quiz-meta">{attempt.status.replaceAll("_", " ")}</span>;
}

function UnitQuizMeta({ quiz }: { quiz: QuizSummary | null }) {
  if (!quiz?.available) return null;

  if (quiz.in_progress_attempt_id) {
    return <p className="subtopic-card__quiz-meta">Unfinished · start again</p>;
  }
  if (!quiz.unlocked) {
    return <p className="subtopic-card__quiz-meta">Complete lesson to unlock</p>;
  }

  const latest = trackedAttempts(quiz.recent_attempts)[0];
  if (!latest) {
    return <p className="subtopic-card__quiz-meta">Ready to take</p>;
  }

  const when = formatAttemptWhen(latest);
  return (
    <p className="subtopic-card__quiz-meta">
      {when ? <>Last quiz {when} · </> : "Last quiz · "}
      <QuizAttemptResult attempt={latest} />
    </p>
  );
}

function TopicCard({
  subjectId,
  topic,
  index,
}: {
  subjectId: string;
  topic: TopicNode;
  index: number;
}) {
  const quiz = topic.overall_quiz;
  const lessonDone = Boolean(topic.topic_lesson_completed);
  const passedLabel = passedBestLabel(quiz);
  const retake = latestRetakeLabel(quiz);
  const status = passedLabel
    ? { label: passedLabel, cls: "badge--ok" }
    : unitStatus(lessonDone, quiz, null, topic.progress_percent);
  const href = topic.has_topic_lesson
    ? topicLessonPath(subjectId, topic.id)
    : `/subjects/${subjectId}/topics/${topic.id}`;

  return (
    <li className="subtopic-card">
      <Link to={href} className="subtopic-card__head">
        <div className="list-item__num">{String(index + 1).padStart(2, "0")}</div>
        <div>
          <p className="list-item__title">{topic.title}</p>
          <p className="list-item__meta">{Math.round(topic.progress_percent)}% complete</p>
        </div>
        <div className="subtopic-card__quiz-col">
          <span className={`badge ${status.cls}`}>{status.label}</span>
          {passedLabel ? (
            retake ? <p className="subtopic-card__quiz-meta">{retake}</p> : null
          ) : (
            <UnitQuizMeta quiz={quiz} />
          )}
        </div>
        <span className="subtopic-card__chevron" aria-hidden="true">
          ›
        </span>
      </Link>
    </li>
  );
}

export function SchoolMaterialPanel({
  subjectId,
  topics,
}: {
  subjectId: string;
  subjectName: string;
  topics: TopicNode[];
}) {
  return (
    <div className="topic-layout">
      <div className="topic-layout__main">
        <section className="school-section school-section--units" aria-labelledby="topics-heading">
          <header className="school-section__head">
            <h2 id="topics-heading">Topics</h2>
            <p>Open a topic lesson, then take its quiz.</p>
          </header>
          {topics.length === 0 ? (
            <p className="muted">No topics published for this subject yet.</p>
          ) : (
            <ul className="list">
              {topics.map((topic, index) => (
                <TopicCard key={topic.id} subjectId={subjectId} topic={topic} index={index} />
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
