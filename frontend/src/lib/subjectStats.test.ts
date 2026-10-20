import { describe, expect, it } from "vitest";

import type { SubjectNode } from "../api/types";
import { subjectStats, summarizeSubjectStats } from "./subjectStats";

function subject(overrides: Partial<SubjectNode> = {}): SubjectNode {
  return {
    id: "math-1",
    code: "MATH",
    name: "Mathematics",
    grade_name: "Grade 8",
    academic_period_name: "2026-27",
    progress_percent: 25,
    topics: [
      {
        id: "topic-1",
        title: "Approved Materials",
        slug: "approved_materials",
        sequence: 1,
        progress_percent: 25,
        complete: false,
        objectives: [],
        has_topic_lesson: false,
        topic_lesson_completed: false,
        overall_quiz: {
          id: "overall-1",
          title: "Overall",
          scope: "topic_mastery",
          available: true,
          unlocked: false,
          locked_reason: null,
          pass_threshold_percent: 70,
          attempt_count: 0,
          best_score_percent: null,
          passed: false,
          in_progress_attempt_id: null,
          recent_attempts: [],
        },
        subtopics: [
          {
            id: "st-1",
            title: "Rectangles",
            slug: "rectangles",
            sequence: 1,
            has_lesson: true,
            lesson_completed: true,
            progress_percent: 50,
            quiz: {
              id: "quiz-1",
              title: "Quiz",
              scope: "subtopic_mastery",
              available: true,
              unlocked: true,
              locked_reason: null,
              pass_threshold_percent: 70,
              attempt_count: 1,
              best_score_percent: 50,
              passed: false,
              in_progress_attempt_id: null,
              recent_attempts: [],
            },
          },
        ],
      },
    ],
    ...overrides,
  };
}

describe("subjectStats", () => {
  it("summarizes topic, lesson, and quiz progress already on the directory", () => {
    const row = subjectStats(subject());

    expect(row).toMatchObject({
      name: "Mathematics",
      code: "MATH",
      status: "in_progress",
      href: "/subjects/math-1",
      topicsDone: 0,
      topicsTotal: 1,
      progressPercent: 25,
      lessonsDone: 1,
      lessonsTotal: 1,
      quizzesPassed: 0,
      quizzesAvailable: 2,
      attemptCount: 1,
      bestScorePercent: 50,
    });
  });

  it("marks an empty subject as coming soon and a finished one as complete", () => {
    const empty = subjectStats(subject({ topics: [] }));
    expect(empty.status).toBe("coming_soon");
    expect(empty.href).toBeNull();
    expect(empty.bestScorePercent).toBeNull();

    const done = subjectStats(
      subject({
        topics: [
          {
            ...subject().topics[0],
            complete: true,
            progress_percent: 100,
          },
        ],
      }),
    );
    expect(done.status).toBe("complete");
    expect(done.progressPercent).toBe(100);
  });

  it("averages progress across enrolled subjects", () => {
    const summary = summarizeSubjectStats([
      subjectStats(subject()),
      subjectStats(subject({ id: "sci-1", name: "Science", topics: [] })),
    ]);

    expect(summary).toMatchObject({
      subjects: 2,
      topicsDone: 0,
      topicsTotal: 1,
      lessonsDone: 1,
      lessonsTotal: 1,
      quizzesPassed: 0,
      quizzesAvailable: 2,
      averageProgress: 25,
    });
  });
});
