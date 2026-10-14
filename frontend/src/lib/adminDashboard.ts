/** Pure helpers behind the admin dashboard -- kept out of the page so they are easy to test. */

import type { AtRiskFlag } from "../api/atRisk";
import type { SubjectSummary } from "../api/insights";
import type { AdminGrade } from "./adminCurriculumLive";

export type AtRiskSummary = {
  urgent: number;
  attention: number;
  monitor: number;
  /** Active flags. One student can carry several (one per subject, plus attendance). */
  flags: number;
  /** Distinct students behind those flags. */
  students: number;
};

export function summariseAtRisk(flags: AtRiskFlag[]): AtRiskSummary {
  return {
    urgent: flags.filter((flag) => flag.tier === "urgent").length,
    attention: flags.filter((flag) => flag.tier === "attention").length,
    monitor: flags.filter((flag) => flag.tier === "monitor").length,
    flags: flags.length,
    students: new Set(flags.map((flag) => flag.student_id)).size,
  };
}

/**
 * Evidence rules from docs/design/06-analytics-and-comparison-insights.md (section 2 and 4):
 * never draw a conclusion about a group from fewer than 5 scored students, and treat a result
 * as incomplete when under 80% of the group took part.
 */
export const MIN_SCORED_STUDENTS = 5;
export const MIN_PARTICIPATION_PERCENT = 80;

/** At or above this, a subject is doing well: it is the quiz pass mark, and the line teacher
 * analytics already uses for "strong" (`tierFor` in teacherAnalytics.ts). */
export const STRONG_MASTERY_PERCENT = 70;

/** Enough students have quiz results for the subject's average to mean something. */
export function hasEnoughEvidence(subject: SubjectSummary): boolean {
  return subject.average_mastery !== null && subject.students_attempted >= MIN_SCORED_STUDENTS;
}

/** Fewer than 80% of enrolled students have taken a quiz, so the average may not be
 * representative of the whole group. */
export function isLowParticipation(subject: SubjectSummary): boolean {
  if (subject.students === 0) return false;
  return (subject.students_attempted / subject.students) * 100 < MIN_PARTICIPATION_PERCENT;
}

/** Below the 70% "strong" line, so admin and teacher views agree on what counts as a problem. */
export function isBelowStrong(subject: SubjectSummary): boolean {
  return (subject.average_mastery ?? 0) < STRONG_MASTERY_PERCENT;
}

/** Subjects with enough evidence that average below 70%, weakest first. Subjects with too few
 * scored students are left out -- an average of two or three students says little -- and so
 * are subjects already doing well, so the list never names a healthy subject just because it
 * happens to be the lowest. */
export function weakestSubjects(subjects: SubjectSummary[], limit = 5): SubjectSummary[] {
  return subjects
    .filter(hasEnoughEvidence)
    .filter(isBelowStrong)
    .sort((a, b) => (a.average_mastery ?? 0) - (b.average_mastery ?? 0))
    .slice(0, limit);
}

/** Subjects that have some quiz results, but not yet from enough students to compare. */
export function countTooFewToCompare(subjects: SubjectSummary[]): number {
  return subjects.filter(
    (subject) => subject.students_attempted > 0 && !hasEnoughEvidence(subject),
  ).length;
}

/** Share of quiz attempts that passed, or null when there were none. */
export function passRate(subject: SubjectSummary): number | null {
  if (subject.quizzes_taken === 0) return null;
  return (subject.quizzes_passed / subject.quizzes_taken) * 100;
}

export type SubjectCoverage = {
  id: string;
  name: string;
  topics: number;
  /** Published quizzes of both kinds: one per subtopic, plus a topic-wide mastery quiz. */
  quizzes: number;
};

export type GradeCoverage = {
  key: string;
  name: string;
  subjects: SubjectCoverage[];
  /** Subjects with no published topics yet -- where an admin should upload next. */
  emptySubjects: number;
};

export type CurriculumCoverage = {
  grades: GradeCoverage[];
  totalSubjects: number;
  subjectsWithContent: number;
  totalTopics: number;
};

export function curriculumCoverage(grades: AdminGrade[]): CurriculumCoverage {
  let totalSubjects = 0;
  let subjectsWithContent = 0;
  let totalTopics = 0;

  const rows = grades.map((grade) => {
    const subjects = grade.subjects.map((subject) => ({
      id: subject.id,
      name: subject.name,
      topics: subject.topics.length,
      quizzes: subject.topics.reduce(
        (sum, topic) =>
          sum +
          (topic.masteryQuiz ? 1 : 0) +
          topic.subtopics.filter((subtopic) => subtopic.quiz !== null).length,
        0,
      ),
    }));
    // Empty subjects first: they are the ones that need action.
    subjects.sort((a, b) => a.topics - b.topics || a.name.localeCompare(b.name));

    totalSubjects += subjects.length;
    subjectsWithContent += subjects.filter((subject) => subject.topics > 0).length;
    totalTopics += subjects.reduce((sum, subject) => sum + subject.topics, 0);

    return {
      key: grade.key,
      name: grade.name,
      subjects,
      emptySubjects: subjects.filter((subject) => subject.topics === 0).length,
    };
  });

  return { grades: rows, totalSubjects, subjectsWithContent, totalTopics };
}

export type CoverageMatrix = {
  /** Every subject name offered in any grade, alphabetical -- the table's columns. */
  subjects: string[];
  rows: {
    key: string;
    name: string;
    emptySubjects: number;
    /** One per column; null where the grade does not offer that subject at all. */
    cells: (SubjectCoverage | null)[];
  }[];
};

/** Grades as rows and subjects as columns, so a gap stands out at a glance. Subjects are
 * matched by name, since the same subject has a different id in each grade. */
export function coverageMatrix(coverage: CurriculumCoverage): CoverageMatrix {
  const names = new Set<string>();
  for (const grade of coverage.grades) {
    for (const subject of grade.subjects) names.add(subject.name);
  }
  const subjects = [...names].sort((a, b) => a.localeCompare(b));

  return {
    subjects,
    rows: coverage.grades.map((grade) => ({
      key: grade.key,
      name: grade.name,
      emptySubjects: grade.emptySubjects,
      cells: subjects.map(
        (name) => grade.subjects.find((subject) => subject.name === name) ?? null,
      ),
    })),
  };
}

export function formatPercent(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}%`;
}

export function plural(count: number, word: string, pluralWord = `${word}s`): string {
  return `${count} ${count === 1 ? word : pluralWord}`;
}
