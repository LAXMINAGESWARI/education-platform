import type {
  LearningDirectory,
  MaterialProgress,
  QuizSummary,
  SubjectNode,
  SubtopicNode,
  TopicNode,
} from "../api/types";
import type { StatusKind } from "./badges";
import { trackedAttempts } from "./quizAction";

const SUBJECT_BLURBS: Record<string, string> = {
  Mathematics: "Number sense, algebra foundations, and geometry basics.",
  MATH: "Number sense, algebra foundations, and geometry basics.",
};

export function subjectBlurb(
  subject: Pick<SubjectNode, "name" | "code" | "grade_name" | "academic_period_name">,
): string {
  return (
    SUBJECT_BLURBS[subject.name] ??
    SUBJECT_BLURBS[subject.code] ??
    `${subject.grade_name} · ${subject.academic_period_name}`
  );
}

/** POC school curriculum lives in the first (and usually only) published topic bucket. */
export function schoolTopic(subject: SubjectNode): TopicNode | null {
  return subject.topics[0] ?? null;
}

export function subjectSubtopics(subject: SubjectNode) {
  return subject.topics.flatMap((topic) => topic.subtopics);
}

export type SubjectUnitEntry = {
  topic: TopicNode;
  subtopic: SubtopicNode | null;
};

function publishedLessonSubtopics(topic: TopicNode): SubtopicNode[] {
  return topic.subtopics.filter((subtopic) => subtopic.has_lesson);
}

/** One card per topic. Subtopic lessons stay off the subject page. */
export function subjectUnitEntries(topics: TopicNode[]): SubjectUnitEntry[] {
  return topics.map((topic) => ({ topic, subtopic: null }));
}

export function subjectTopicLessonTopics(topics: TopicNode[]): TopicNode[] {
  return topics.filter(
    (topic) => hasPublishedTopicLesson(topic) && publishedLessonSubtopics(topic).length > 0,
  );
}

export function subjectProgress(subject: SubjectNode): {
  done: number;
  total: number;
  pct: number;
} {
  if (subject.done != null && subject.total != null) {
    return {
      done: subject.done,
      total: subject.total,
      pct: subject.progress_percent,
    };
  }
  const total = subject.topics.length;
  const done = subject.topics.filter((topic) => topic.complete).length;
  return {
    done,
    total,
    pct: total === 0 ? 0 : Math.round((100 * done) / total),
  };
}

function topicHasStarted(topic: TopicNode): boolean {
  if (topic.complete || topic.progress_percent > 0) return true;
  if (topic.topic_lesson_completed || topic.topic_lesson_progress) return true;
  if ((topic.overall_quiz?.attempt_count ?? 0) > 0) return true;
  return topic.subtopics.some(
    (subtopic) =>
      subtopic.lesson_completed ||
      subtopic.progress_percent > 0 ||
      subtopic.progress != null ||
      (subtopic.quiz?.attempt_count ?? 0) > 0,
  );
}

/** Status for the subject the student just opened. Uses directory fields only. */
export function subjectStudyStatus(subject: SubjectNode): StatusKind {
  if (subject.topics.length === 0) return "coming_soon";
  const { done, total } = subjectProgress(subject);
  if (total > 0 && done >= total) return "complete";
  if (done > 0 || subject.topics.some(topicHasStarted)) return "in_progress";
  return "not_started";
}

/** Lesson and quiz totals for the topic cards on the subject page. */
export function subjectCardCounts(topics: TopicNode[]): {
  lessonsDone: number;
  lessonsTotal: number;
  quizzesPassed: number;
  quizzesAvailable: number;
} {
  let lessonsDone = 0;
  let lessonsTotal = 0;
  let quizzesPassed = 0;
  let quizzesAvailable = 0;
  for (const topic of topics) {
    if (topic.has_topic_lesson) {
      lessonsTotal += 1;
      if (topic.topic_lesson_completed) lessonsDone += 1;
    }
    const quiz = topic.overall_quiz;
    if (quiz?.available) {
      quizzesAvailable += 1;
      if (quiz.passed) quizzesPassed += 1;
    }
  }
  return { lessonsDone, lessonsTotal, quizzesPassed, quizzesAvailable };
}

export function topicPagePath(subjectId: string, topicId: string): string {
  return `/subjects/${subjectId}/topics/${topicId}`;
}

export function topicLessonPath(subjectId: string, topicId: string): string {
  return `/subjects/${subjectId}/topics/${topicId}/lesson`;
}

export function topicHistoryPath(subjectId: string, topicId: string): string {
  return `/subjects/${subjectId}/topics/${topicId}/history`;
}

export type TopicLessonEntry = {
  action: "Start" | "Continue" | "Review";
  detail: string;
};

export function topicLessonEntry(
  topic: Pick<TopicNode, "topic_lesson_completed" | "topic_lesson_progress">,
  progress?: MaterialProgress | null,
): TopicLessonEntry {
  const lessonProgress = progress ?? topic.topic_lesson_progress;
  if (topic.topic_lesson_completed || lessonProgress?.status === "completed") {
    return { action: "Review", detail: "Lesson complete" };
  }
  if (lessonProgress?.status === "opened") {
    const slide = lessonProgress.last_unit_ordinal;
    return {
      action: "Continue",
      detail: slide ? `In progress · slide ${slide}` : "In progress",
    };
  }
  return { action: "Start", detail: "Not started" };
}

export type TopicQuizEntry = {
  action: "Take quiz" | "Retake";
  detail: string;
  href: string | null;
};

export function topicQuizEntry(quiz: QuizSummary | null | undefined): TopicQuizEntry {
  if (!quiz?.available || !quiz.id) {
    return { action: "Take quiz", detail: "No quiz yet", href: null };
  }
  const finished = trackedAttempts(quiz.recent_attempts).find(
    (attempt) => attempt.status !== "in_progress",
  );
  const action = finished ? "Retake" : "Take quiz";
  if (!quiz.unlocked) {
    return { action, detail: "Complete the topic lesson first.", href: null };
  }
  const score =
    finished?.score_percent == null ? null : Math.round(Number(finished.score_percent));
  return {
    action,
    detail: score == null ? "Ready" : `Last score ${score}%`,
    href: `/quizzes/${quiz.id}`,
  };
}

export function hasPublishedTopicLesson(topic: TopicNode): boolean {
  return Boolean(topic.has_topic_lesson);
}

export function findSubjectTopic(
  directory: LearningDirectory,
  subjectId: string,
  topicId: string,
): { subject: SubjectNode; topic: TopicNode } | null {
  const subject = directory.subjects.find((item) => item.id === subjectId);
  const topic = subject?.topics.find((item) => item.id === topicId);
  if (!subject || !topic) return null;
  return { subject, topic };
}

export type OverallQuizGate = "topic_lesson" | "subtopic_quizzes";

export function overallQuizGate(topic: TopicNode): OverallQuizGate {
  return hasPublishedTopicLesson(topic) ? "topic_lesson" : "subtopic_quizzes";
}

export type OverallQuizCopy = {
  eyebrow: string;
  statusLine: string;
  body: string;
};

export function overallQuizCopy(topic: TopicNode): OverallQuizCopy {
  const quiz = topic.overall_quiz;
  const gate = overallQuizGate(topic);
  const unlocked = Boolean(quiz?.unlocked);
  const passed = Boolean(quiz?.passed);

  switch (gate) {
    case "topic_lesson":
      return {
        eyebrow: "From the topic lesson",
        statusLine: unlocked
          ? passed
            ? "Passed · subject complete"
            : "Unlocked · ready to take"
          : "Locked until the topic lesson is complete",
        body: unlocked
          ? passed
            ? "You passed the topic quiz."
            : "The topic lesson is complete. You can take the topic quiz now."
          : "Complete the topic lesson first.",
      };
    case "subtopic_quizzes":
      return {
        eyebrow: "After subtopic quizzes",
        statusLine: unlocked
          ? passed
            ? "Passed · subject complete"
            : "Unlocked · ready to take"
          : "Locked until every subtopic quiz is passed",
        body: unlocked
          ? passed
            ? "You passed the topic quiz."
            : "All subtopic quizzes passed. You can take the topic quiz."
          : "Finish every subtopic quiz to unlock this topic quiz.",
      };
    default: {
      const _exhaustive: never = gate;
      return _exhaustive;
    }
  }
}
