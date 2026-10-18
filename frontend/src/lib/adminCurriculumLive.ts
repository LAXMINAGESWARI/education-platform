/** Adapt live learning-directory into the admin materials browse tree. */

import type { LearningDirectory, SubjectNode, TopicNode } from "../api/types";
import { DEMO_ADMIN, DEMO_PASSWORD } from "../auth/demoAccounts";

/** Live catalog is published-only (API does not expose drafts). */
export type PublishStatus = "published";

export type AdminQuiz = {
  id: string;
  title: string;
  kind: "subtopic" | "topic_mastery";
  status: PublishStatus;
};

export type AdminLesson = {
  id: string;
  title: string;
  status: PublishStatus;
  /** Subtopic id for optional material GET. */
  subtopicId: string;
};

export type AdminSubtopic = {
  id: string;
  title: string;
  order: number;
  lesson: AdminLesson | null;
  quiz: AdminQuiz | null;
};

export type AdminTopic = {
  id: string;
  title: string;
  unitLabel: string;
  order: number;
  status: PublishStatus;
  hasTopicLesson: boolean;
  subtopics: AdminSubtopic[];
  masteryQuiz: AdminQuiz | null;
};

export type AdminSubject = {
  id: string;
  name: string;
  code: string;
  blurb: string;
  topics: AdminTopic[];
};

export type AdminGrade = {
  /** Slugified `grade_name` used in routes as `:gradeKey`. */
  key: string;
  name: string;
  number: number | null;
  subjects: AdminSubject[];
};

const SUBJECT_BLURBS: Record<string, string> = {
  Mathematics: "Number sense, algebra foundations, and geometry basics.",
  MATH: "Number sense, algebra foundations, and geometry basics.",
};

export function slugifyGradeKey(gradeName: string): string {
  return gradeName
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function parseGradeNumber(gradeName: string): number | null {
  const match = gradeName.match(/(\d+)/);
  return match ? Number(match[1]) : null;
}

function subjectBlurb(subject: SubjectNode): string {
  return (
    SUBJECT_BLURBS[subject.name] ??
    SUBJECT_BLURBS[subject.code] ??
    `${subject.grade_name} · ${subject.academic_period_name}`
  );
}

function mapTopic(topic: TopicNode): AdminTopic {
  const subtopics: AdminSubtopic[] = topic.subtopics.map((st) => ({
    id: st.id,
    title: st.title,
    order: st.sequence,
    lesson: st.has_lesson
      ? {
          id: st.id,
          title: `${st.title} · lesson`,
          status: "published",
          subtopicId: st.id,
        }
      : null,
    quiz: st.quiz
      ? {
          id: st.quiz.id,
          title: st.quiz.title,
          kind: "subtopic",
          status: "published",
        }
      : null,
  }));

  return {
    id: topic.id,
    title: topic.title,
    unitLabel: `Unit ${topic.sequence}`,
    order: topic.sequence,
    status: "published",
    hasTopicLesson: Boolean(topic.has_topic_lesson),
    subtopics,
    masteryQuiz: topic.overall_quiz
      ? {
          id: topic.overall_quiz.id,
          title: topic.overall_quiz.title,
          kind: "topic_mastery",
          status: "published",
        }
      : null,
  };
}

function mapSubject(subject: SubjectNode): AdminSubject {
  const topics = [...subject.topics]
    .sort((a, b) => a.sequence - b.sequence)
    .map(mapTopic);
  return {
    id: subject.id,
    name: subject.name,
    code: subject.code,
    blurb: subjectBlurb(subject),
    topics,
  };
}

/** Group directory subjects by grade_name into browse grades. */
export function adaptLearningDirectory(directory: LearningDirectory): AdminGrade[] {
  const byGrade = new Map<string, SubjectNode[]>();
  for (const subject of directory.subjects) {
    const name = subject.grade_name.trim() || "Unknown grade";
    const list = byGrade.get(name) ?? [];
    list.push(subject);
    byGrade.set(name, list);
  }

  const grades: AdminGrade[] = [];
  for (const [name, subjects] of byGrade) {
    grades.push({
      key: slugifyGradeKey(name),
      name,
      number: parseGradeNumber(name),
      subjects: subjects.map(mapSubject),
    });
  }

  grades.sort((a, b) => {
    if (a.number != null && b.number != null) return a.number - b.number;
    if (a.number != null) return -1;
    if (b.number != null) return 1;
    return a.name.localeCompare(b.name);
  });

  return grades;
}

export function getAdminGrade(
  grades: AdminGrade[],
  gradeKey: string,
): AdminGrade | undefined {
  return grades.find((g) => g.key === gradeKey);
}

export function getAdminSubject(
  grades: AdminGrade[],
  gradeKey: string,
  subjectId: string,
): { grade: AdminGrade; subject: AdminSubject } | undefined {
  const grade = getAdminGrade(grades, gradeKey);
  if (!grade) return undefined;
  const subject = grade.subjects.find((s) => s.id === subjectId);
  if (!subject) return undefined;
  return { grade, subject };
}

export function getAdminTopic(
  grades: AdminGrade[],
  gradeKey: string,
  subjectId: string,
  topicId: string,
): { grade: AdminGrade; subject: AdminSubject; topic: AdminTopic } | undefined {
  const found = getAdminSubject(grades, gradeKey, subjectId);
  if (!found) return undefined;
  const topic = found.subject.topics.find((t) => t.id === topicId);
  if (!topic) return undefined;
  return { ...found, topic };
}

export type AdminUnitCard = {
  key: string;
  title: string;
  topicId: string;
  /** Subtopic id when this card is a chapter under a parent topic. */
  unitId: string | null;
  hasLesson: boolean;
  quizTitle: string | null;
};

function publishedLessonSubtopics(topic: AdminTopic): AdminSubtopic[] {
  return topic.subtopics.filter((subtopic) => subtopic.lesson != null);
}

/** Chapter cards: published subtopic lessons, or one topic card when none exist. */
export function subjectUnitCards(subject: AdminSubject): AdminUnitCard[] {
  const cards: AdminUnitCard[] = [];
  for (const topic of subject.topics) {
    const lessonUnits = publishedLessonSubtopics(topic);
    if (lessonUnits.length > 0) {
      for (const subtopic of lessonUnits) {
        cards.push({
          key: subtopic.id,
          title: subtopic.title,
          topicId: topic.id,
          unitId: subtopic.id,
          hasLesson: true,
          quizTitle: subtopic.quiz?.title ?? topic.masteryQuiz?.title ?? null,
        });
      }
      continue;
    }
    cards.push({
      key: topic.id,
      title: topic.title,
      topicId: topic.id,
      unitId: null,
      hasLesson: topic.hasTopicLesson,
      quizTitle: topic.masteryQuiz?.title ?? null,
    });
  }
  return cards;
}

/** Parent topics with a published topic lesson and published subtopic lesson cards. */
export function subjectTopicLessonTopics(subject: AdminSubject): AdminTopic[] {
  return subject.topics.filter(
    (topic) => topic.hasTopicLesson && publishedLessonSubtopics(topic).length > 0,
  );
}

export function adminTopicPath(
  gradeKey: string,
  subjectId: string,
  topicId: string,
  options?: { published?: boolean; unitId?: string | null },
): string {
  const path = `/admin/materials/grades/${gradeKey}/subjects/${subjectId}/topics/${topicId}`;
  const params = new URLSearchParams();
  if (options?.published) params.set("tab", "published");
  if (options?.unitId) params.set("unit", options.unitId);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export function countTopics(subject: AdminSubject): number {
  return subject.topics.length;
}

export function gradeSummary(grade: AdminGrade): {
  subjects: number;
  topics: number;
} {
  let topics = 0;
  for (const subject of grade.subjects) {
    topics += subject.topics.length;
  }
  return { subjects: grade.subjects.length, topics };
}

/** Clear error when a DEV fixture session tries to load live materials. */
export const MOCK_SESSION_MATERIALS_ERROR =
  `Materials need a real admin JWT. Sign in as ${DEMO_ADMIN.email} / ${DEMO_PASSWORD} (or use Enter as admin). Fixture mock sessions cannot call the live API.`;
