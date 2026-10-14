import { describe, expect, it } from "vitest";

import type { AtRiskFlag } from "../api/atRisk";
import type { SubjectSummary } from "../api/insights";
import type { AdminGrade, AdminTopic } from "./adminCurriculumLive";
import {
  countTooFewToCompare,
  coverageMatrix,
  curriculumCoverage,
  formatPercent,
  isLowParticipation,
  passRate,
  plural,
  summariseAtRisk,
  weakestSubjects,
} from "./adminDashboard";

function flag(id: string, studentId: string, tier: AtRiskFlag["tier"]): AtRiskFlag {
  return {
    id,
    student_id: studentId,
    student_name: studentId,
    grade_subject_offering_id: null,
    subject: null,
    tier,
    drivers: [],
    status: "active",
    dismissed_by_user_id: null,
    dismissal_note: null,
  };
}

function subject(over: Partial<SubjectSummary>): SubjectSummary {
  return {
    grade: "Grade 8",
    subject: "Mathematics",
    students: 10,
    students_attempted: 5,
    average_mastery: 60,
    quizzes_taken: 10,
    quizzes_passed: 5,
    ...over,
  };
}

function topic(id: string, withQuiz: boolean, subtopicQuizzes = 0): AdminTopic {
  return {
    id,
    title: id,
    unitLabel: "Unit 1",
    order: 1,
    status: "published",
    hasTopicLesson: true,
    subtopics: Array.from({ length: subtopicQuizzes }, (_, index) => ({
      id: `${id}-st-${index}`,
      title: `Subtopic ${index}`,
      order: index + 1,
      lesson: null,
      quiz: { id: `${id}-q-${index}`, title: "Quiz", kind: "subtopic", status: "published" },
    })),
    masteryQuiz: withQuiz
      ? { id: `${id}-quiz`, title: "Quiz", kind: "topic_mastery", status: "published" }
      : null,
  };
}

describe("summariseAtRisk", () => {
  it("counts flags per tier and distinct students", () => {
    const counts = summariseAtRisk([
      flag("f1", "s1", "urgent"),
      flag("f2", "s1", "monitor"),
      flag("f3", "s2", "attention"),
    ]);
    expect(counts).toEqual({ urgent: 1, attention: 1, monitor: 1, flags: 3, students: 2 });
  });
});

describe("weakestSubjects", () => {
  it("sorts by mastery, drops never-attempted subjects, and caps the list", () => {
    const result = weakestSubjects(
      [
        subject({ subject: "Science", average_mastery: 72 }),
        subject({ subject: "Art", average_mastery: null, students_attempted: 0 }),
        subject({ subject: "English", average_mastery: 41 }),
        subject({ subject: "History", average_mastery: 55 }),
      ],
      2,
    );
    expect(result.map((s) => s.subject)).toEqual(["English", "History"]);
  });

  it("leaves out subjects already at or above 70%", () => {
    const result = weakestSubjects([
      subject({ subject: "Science", average_mastery: 70 }),
      subject({ subject: "English", average_mastery: 69.9 }),
    ]);
    expect(result.map((s) => s.subject)).toEqual(["English"]);
  });

  it("leaves out subjects with fewer than 5 scored students", () => {
    const subjects = [
      subject({ subject: "Music", average_mastery: 20, students_attempted: 4 }),
      subject({ subject: "English", average_mastery: 41, students_attempted: 5 }),
    ];
    expect(weakestSubjects(subjects).map((s) => s.subject)).toEqual(["English"]);
    expect(countTooFewToCompare(subjects)).toBe(1);
  });
});

describe("isLowParticipation", () => {
  it("flags subjects where under 80% of students took a quiz", () => {
    expect(isLowParticipation(subject({ students: 10, students_attempted: 7 }))).toBe(true);
    expect(isLowParticipation(subject({ students: 10, students_attempted: 8 }))).toBe(false);
    expect(isLowParticipation(subject({ students: 0, students_attempted: 0 }))).toBe(false);
  });
});

describe("passRate", () => {
  it("is null with no attempts", () => {
    expect(passRate(subject({ quizzes_taken: 0, quizzes_passed: 0 }))).toBeNull();
    expect(passRate(subject({ quizzes_taken: 8, quizzes_passed: 2 }))).toBe(25);
  });
});

describe("curriculumCoverage", () => {
  it("totals topics and lists empty subjects first", () => {
    const grades: AdminGrade[] = [
      {
        key: "grade-8",
        name: "Grade 8",
        number: 8,
        subjects: [
          {
            id: "math",
            name: "Mathematics",
            code: "MATH",
            blurb: "",
            topics: [topic("t1", true), topic("t2", false, 5)],
          },
          { id: "sci", name: "Science", code: "SCI", blurb: "", topics: [] },
        ],
      },
    ];

    const coverage = curriculumCoverage(grades);
    expect(coverage.totalSubjects).toBe(2);
    expect(coverage.subjectsWithContent).toBe(1);
    expect(coverage.totalTopics).toBe(2);
    expect(coverage.grades[0].emptySubjects).toBe(1);
    expect(coverage.grades[0].subjects.map((s) => s.name)).toEqual(["Science", "Mathematics"]);
    // One topic-wide quiz on t1, plus five subtopic quizzes on t2.
    expect(coverage.grades[0].subjects[1].quizzes).toBe(6);
  });
});

describe("coverageMatrix", () => {
  it("lines grades up by subject name and leaves a gap where a grade lacks a subject", () => {
    const grades: AdminGrade[] = [
      {
        key: "grade-8",
        name: "Grade 8",
        number: 8,
        subjects: [
          { id: "g8-math", name: "Mathematics", code: "MATH", blurb: "", topics: [topic("t1", true)] },
          { id: "g8-art", name: "Art", code: "ART", blurb: "", topics: [] },
        ],
      },
      {
        key: "grade-9",
        name: "Grade 9",
        number: 9,
        subjects: [
          { id: "g9-math", name: "Mathematics", code: "MATH", blurb: "", topics: [topic("t2", false, 2)] },
        ],
      },
    ];

    const matrix = coverageMatrix(curriculumCoverage(grades));
    expect(matrix.subjects).toEqual(["Art", "Mathematics"]);
    expect(matrix.rows[0].cells.map((cell) => cell?.id ?? null)).toEqual(["g8-art", "g8-math"]);
    expect(matrix.rows[1].cells.map((cell) => cell?.id ?? null)).toEqual([null, "g9-math"]);
    expect(matrix.rows[1].cells[1]?.quizzes).toBe(2);
  });
});

describe("plural", () => {
  it("handles irregular plurals", () => {
    expect(plural(1, "quiz", "quizzes")).toBe("1 quiz");
    expect(plural(5, "quiz", "quizzes")).toBe("5 quizzes");
    expect(plural(2, "topic")).toBe("2 topics");
  });
});

describe("formatPercent", () => {
  it("shows a dash for missing values", () => {
    expect(formatPercent(null)).toBe("—");
    expect(formatPercent(63.26)).toBe("63.3%");
  });
});
