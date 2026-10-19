import type { AttemptResult, LearningDirectory, StartAttemptResponse, TopicNode } from "../api/types";
import { hasPublishedTopicLesson, topicLessonPath } from "./subjectMaterial";

export type LearningPath = {
  subjectId: string;
  subjectName: string;
  topicId: string;
  topicTitle: string;
  subtopicId: string | null;
  subtopicTitle: string | null;
  subjectPath: string;
  /** @deprecated Use subjectPath */
  topicPath: string;
  lessonPath: string | null;
  /** Unit lesson overview (quiz pane is on this page) */
  quizTabPath: string | null;
  slidesPath: string | null;
  quizHistoryPath: string | null;
  overallUnlocked: boolean;
  topicComplete: boolean;
  hasTopicLesson: boolean;
};

function pathForTopic(
  subjectId: string,
  subjectName: string,
  topic: TopicNode,
  subtopic: TopicNode["subtopics"][number] | null,
): LearningPath {
  const subjectPath = `/subjects/${subjectId}`;
  const hasTopicLesson = hasPublishedTopicLesson(topic);
  const publishedTopicLessonPath = topicLessonPath(subjectId, topic.id);
  const lessonPath = subtopic
    ? `${subjectPath}/subtopics/${subtopic.id}/lesson`
    : hasTopicLesson
      ? publishedTopicLessonPath
      : null;
  return {
    subjectId,
    subjectName,
    topicId: topic.id,
    topicTitle: topic.title,
    subtopicId: subtopic?.id ?? null,
    subtopicTitle: subtopic?.title ?? null,
    subjectPath,
    topicPath: subjectPath,
    lessonPath,
    quizTabPath: lessonPath,
    slidesPath:
      subtopic && lessonPath
        ? `${lessonPath}/slides`
        : hasTopicLesson && lessonPath
          ? lessonPath
          : null,
    quizHistoryPath: subtopic && lessonPath ? `${lessonPath}/history` : null,
    overallUnlocked: Boolean(topic.overall_quiz?.unlocked),
    topicComplete: topic.complete,
    hasTopicLesson,
  };
}

export function resolveLearningPath(
  directory: LearningDirectory,
  scope: "subtopic_mastery" | "topic_mastery" | null | undefined,
  targetId: string | null | undefined,
): LearningPath | null {
  if (!targetId || !scope) return null;

  switch (scope) {
    case "topic_mastery":
      for (const subject of directory.subjects) {
        for (const topic of subject.topics) {
          if (topic.id === targetId) {
            return pathForTopic(subject.id, subject.name, topic, null);
          }
        }
      }
      return null;
    case "subtopic_mastery":
      for (const subject of directory.subjects) {
        for (const topic of subject.topics) {
          const subtopic = topic.subtopics.find((item) => item.id === targetId);
          if (subtopic) {
            return pathForTopic(subject.id, subject.name, topic, subtopic);
          }
        }
      }
      return null;
    default: {
      const _exhaustive: never = scope;
      return _exhaustive;
    }
  }
}

export function resolvePathFromAttempt(
  directory: LearningDirectory,
  attempt: Pick<StartAttemptResponse | AttemptResult, "scope" | "target_id">,
): LearningPath | null {
  return resolveLearningPath(directory, attempt.scope, attempt.target_id);
}

export function resolvePathFromQuizId(
  directory: LearningDirectory,
  quizId: string,
): LearningPath | null {
  for (const subject of directory.subjects) {
    for (const topic of subject.topics) {
      if (topic.overall_quiz?.id === quizId) {
        return pathForTopic(subject.id, subject.name, topic, null);
      }
      for (const subtopic of topic.subtopics) {
        if (subtopic.quiz?.id === quizId) {
          return pathForTopic(subject.id, subject.name, topic, subtopic);
        }
      }
    }
  }
  return null;
}

export function learningPathCrumb(path: LearningPath): { label: string; to: string } | null {
  if (path.subtopicTitle && path.quizTabPath) {
    return { label: path.subtopicTitle, to: path.quizTabPath };
  }
  if (path.hasTopicLesson && path.lessonPath) {
    return { label: path.topicTitle, to: path.lessonPath };
  }
  return null;
}
