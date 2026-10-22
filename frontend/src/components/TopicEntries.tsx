import { Link } from "react-router-dom";

import type { MaterialProgress, TopicNode } from "../api/types";
import { topicFeedbackPath } from "../lib/feedbackLinks";
import { topicLessonEntry, topicLessonResumePath, topicQuizEntry } from "../lib/subjectMaterial";

export function TopicEntries({
  subjectId,
  topic,
  progress,
}: {
  subjectId: string;
  topic: TopicNode;
  progress?: MaterialProgress | null;
}) {
  const lessonProgress = progress ?? topic.topic_lesson_progress;
  const lesson = topicLessonEntry(topic, progress);
  const quiz = topicQuizEntry(topic.overall_quiz);
  const lessonHref = topicLessonResumePath(subjectId, topic.id, lessonProgress);

  return (
    <div className="topic-actions">
      <Link className="pill-btn" to={lessonHref}>
        <span className="pill-btn__face">
          <span className="pill-btn__kicker">Lesson</span>
          <span className="pill-btn__action">{lesson.action}</span>
          <span className="pill-btn__detail">{lesson.detail}</span>
        </span>
      </Link>
      {quiz.href ? (
        <Link className="pill-btn" to={quiz.href}>
          <span className="pill-btn__face">
            <span className="pill-btn__kicker">Quiz</span>
            <span className="pill-btn__action">{quiz.action}</span>
            <span className="pill-btn__detail">{quiz.detail}</span>
          </span>
        </Link>
      ) : (
        <div className="pill-btn is-locked">
          <span className="pill-btn__face">
            <span className="pill-btn__kicker">Quiz</span>
            <span className="pill-btn__action">{quiz.action}</span>
            <span className="pill-btn__detail">{quiz.detail}</span>
          </span>
        </div>
      )}
      <Link className="pill-btn" to={topicFeedbackPath(subjectId, topic.id)}>
        <span className="pill-btn__face">
          <span className="pill-btn__action">Feedback</span>
        </span>
      </Link>
    </div>
  );
}
