import { Link } from "react-router-dom";

import type { AttemptHistoryItem, MaterialProgress, QuizSummary, TopicNode } from "../api/types";
import { latestRetakeLabel, passedBestLabel, trackedAttempts } from "../lib/quizAction";
import { topicPagePath } from "../lib/subjectMaterial";
import { formatAttemptWhen } from "./AttemptHistory";
import { TopicEntries } from "./TopicEntries";

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

function UnitQuizMeta({
  quiz,
  topicLesson = false,
}: {
  quiz: QuizSummary | null;
  topicLesson?: boolean;
}) {
  if (!quiz?.available) return null;

  if (quiz.in_progress_attempt_id) {
    return <p className="subtopic-card__quiz-meta">Unfinished · start again</p>;
  }
  if (!quiz.unlocked) {
    return (
      <p className="subtopic-card__quiz-meta">
        {topicLesson ? "Complete the topic lesson first." : "Complete lesson to unlock"}
      </p>
    );
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

function TopicProgress({ percent }: { percent: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(percent)));
  return (
    <div
      className={`progress topic-card__progress ${pct >= 100 ? "progress--complete" : "progress--in-progress"}`}
      aria-hidden="true"
    >
      <span style={{ width: `${pct}%` }} />
    </div>
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
  const topicPath = topicPagePath(subjectId, topic.id);
  const percentLabel = `${Math.round(topic.progress_percent)}% complete`;

  if (topic.has_topic_lesson) {
    return (
      <li className="subtopic-card">
        <div className="subtopic-card__head">
          <div className="list-item__num">{String(index + 1).padStart(2, "0")}</div>
          <div>
            <Link to={topicPath} className="list-item__title">
              {topic.title}
            </Link>
            <p className="list-item__meta">{percentLabel}</p>
            <TopicProgress percent={topic.progress_percent} />
          </div>
        </div>
        <TopicEntries subjectId={subjectId} topic={topic} />
      </li>
    );
  }

  return (
    <li className="subtopic-card">
      <Link to={topicPath} className="subtopic-card__head">
        <div className="list-item__num">{String(index + 1).padStart(2, "0")}</div>
        <div>
          <p className="list-item__title">{topic.title}</p>
          <p className="list-item__meta">{percentLabel}</p>
          <TopicProgress percent={topic.progress_percent} />
        </div>
        <div className="subtopic-card__quiz-col">
          <span className={`badge ${status.cls}`}>{status.label}</span>
          {passedLabel ? (
            retake ? <p className="subtopic-card__quiz-meta">{retake}</p> : null
          ) : (
            <UnitQuizMeta quiz={quiz} topicLesson={false} />
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
  topics: TopicNode[];
}) {
  const topicCount = topics.length === 1 ? "1 topic" : `${topics.length} topics`;

  return (
    <div className="topic-layout">
      <div className="topic-layout__main">
        <section className="school-section school-section--units" aria-labelledby="topics-heading">
          <header className="school-section__head">
            <div>
              <h2 id="topics-heading">Topics</h2>
              <p>Choose the lesson or the quiz for a topic.</p>
            </div>
            {topics.length > 0 ? <p className="school-section__count">{topicCount}</p> : null}
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
