import type { QuizSummary, SubjectNode } from "../api/types";

import type { StatusKind } from "./badges";
import { subjectProgress } from "./subjectMaterial";

export type SubjectStats = {
  id: string;
  name: string;
  code: string;
  gradeName: string;
  periodName: string;
  href: string | null;
  status: StatusKind;
  topicsDone: number;
  topicsTotal: number;
  /** Average of each topic's own progress, so a half-finished lesson still shows. */
  progressPercent: number;
  lessonsDone: number;
  lessonsTotal: number;
  quizzesPassed: number;
  quizzesAvailable: number;
  attemptCount: number;
  bestScorePercent: number | null;
};

export type SubjectStatsSummary = {
  subjects: number;
  topicsDone: number;
  topicsTotal: number;
  lessonsDone: number;
  lessonsTotal: number;
  quizzesPassed: number;
  quizzesAvailable: number;
  averageProgress: number | null;
};

function lessonCounts(subject: SubjectNode): { done: number; total: number } {
  let done = 0;
  let total = 0;
  for (const topic of subject.topics) {
    if (topic.has_topic_lesson) {
      total += 1;
      if (topic.topic_lesson_completed) done += 1;
    }
    for (const subtopic of topic.subtopics) {
      if (!subtopic.has_lesson) continue;
      total += 1;
      if (subtopic.lesson_completed) done += 1;
    }
  }
  return { done, total };
}

function collectQuizzes(subject: SubjectNode): QuizSummary[] {
  const quizzes: QuizSummary[] = [];
  for (const topic of subject.topics) {
    if (topic.overall_quiz?.available) quizzes.push(topic.overall_quiz);
    for (const subtopic of topic.subtopics) {
      if (subtopic.quiz?.available) quizzes.push(subtopic.quiz);
    }
  }
  return quizzes;
}

function hasStarted(subject: SubjectNode): boolean {
  return subject.topics.some((topic) => {
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
  });
}

function studyStatus(subject: SubjectNode, topicsDone: number, topicsTotal: number): StatusKind {
  if (subject.topics.length === 0) return "coming_soon";
  if (topicsTotal > 0 && topicsDone >= topicsTotal) return "complete";
  if (topicsDone > 0 || hasStarted(subject)) return "in_progress";
  return "not_started";
}

export function subjectStats(subject: SubjectNode): SubjectStats {
  const topics = subjectProgress(subject);
  const lessons = lessonCounts(subject);
  const quizzes = collectQuizzes(subject);
  let best: number | null = null;
  let attempts = 0;
  let passed = 0;
  for (const quiz of quizzes) {
    attempts += quiz.attempt_count;
    if (quiz.passed) passed += 1;
    if (quiz.best_score_percent != null) {
      best = best == null ? quiz.best_score_percent : Math.max(best, quiz.best_score_percent);
    }
  }
  const progressPercent =
    subject.topics.length === 0
      ? 0
      : Math.round(
          subject.topics.reduce((sum, topic) => sum + topic.progress_percent, 0) /
            subject.topics.length,
        );
  const status = studyStatus(subject, topics.done, topics.total);
  return {
    id: subject.id,
    name: subject.name,
    code: subject.code,
    gradeName: subject.grade_name,
    periodName: subject.academic_period_name,
    href: status === "coming_soon" ? null : `/subjects/${subject.id}`,
    status,
    topicsDone: topics.done,
    topicsTotal: topics.total,
    progressPercent,
    lessonsDone: lessons.done,
    lessonsTotal: lessons.total,
    quizzesPassed: passed,
    quizzesAvailable: quizzes.length,
    attemptCount: attempts,
    bestScorePercent: best,
  };
}

export function summarizeSubjectStats(rows: SubjectStats[]): SubjectStatsSummary {
  const totals = rows.reduce(
    (sum, row) => ({
      topicsDone: sum.topicsDone + row.topicsDone,
      topicsTotal: sum.topicsTotal + row.topicsTotal,
      lessonsDone: sum.lessonsDone + row.lessonsDone,
      lessonsTotal: sum.lessonsTotal + row.lessonsTotal,
      quizzesPassed: sum.quizzesPassed + row.quizzesPassed,
      quizzesAvailable: sum.quizzesAvailable + row.quizzesAvailable,
    }),
    {
      topicsDone: 0,
      topicsTotal: 0,
      lessonsDone: 0,
      lessonsTotal: 0,
      quizzesPassed: 0,
      quizzesAvailable: 0,
    },
  );
  const withTopics = rows.filter((row) => row.topicsTotal > 0);
  return {
    subjects: rows.length,
    topicsDone: totals.topicsDone,
    topicsTotal: totals.topicsTotal,
    lessonsDone: totals.lessonsDone,
    lessonsTotal: totals.lessonsTotal,
    quizzesPassed: totals.quizzesPassed,
    quizzesAvailable: totals.quizzesAvailable,
    averageProgress:
      withTopics.length === 0
        ? null
        : Math.round(withTopics.reduce((sum, row) => sum + row.progressPercent, 0) / withTopics.length),
  };
}
