import { Link } from "react-router-dom";

import type { MaterialProgress, TopicNode } from "../api/types";
import { topicLessonEntry, topicLessonPath, topicQuizEntry } from "../lib/subjectMaterial";

export function TopicEntries({
  subjectId,
  topic,
  progress,
}: {
  subjectId: string;
  topic: TopicNode;
  progress?: MaterialProgress | null;
}) {
  const lesson = topicLessonEntry(topic, progress);
  const quiz = topicQuizEntry(topic.overall_quiz);

  return (
    <div className="topic-entries">
      <Link className="topic-entry" to={topicLessonPath(subjectId, topic.id)}>
        <p className="topic-entry__kicker">Lesson</p>
        <p className="topic-entry__action">{lesson.action}</p>
        <p className="topic-entry__detail">{lesson.detail}</p>
      </Link>
      {quiz.href ? (
        <Link className="topic-entry" to={quiz.href}>
          <p className="topic-entry__kicker">Quiz</p>
          <p className="topic-entry__action">{quiz.action}</p>
          <p className="topic-entry__detail">{quiz.detail}</p>
        </Link>
      ) : (
        <div className="topic-entry is-locked">
          <p className="topic-entry__kicker">Quiz</p>
          <p className="topic-entry__action">{quiz.action}</p>
          <p className="topic-entry__detail">{quiz.detail}</p>
        </div>
      )}
    </div>
  );
}
