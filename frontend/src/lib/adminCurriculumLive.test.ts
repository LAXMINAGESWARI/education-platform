import { describe, expect, it } from "vitest";

import type { LearningDirectory } from "../api/types";
import {
  adaptLearningDirectory,
  adminTopicPath,
  subjectTopicLessonTopics,
  subjectUnitCards,
} from "./adminCurriculumLive";

const directory: LearningDirectory = {
  subjects: [
    {
      id: "subj-math-uuid",
      code: "MATH",
      name: "Mathematics",
      grade_name: "Grade 8",
      academic_period_name: "2025-2026",
      progress_percent: 0,
      topics: [
        {
          id: "topic-1-uuid",
          title: "Numbers",
          slug: "numbers",
          sequence: 1,
          progress_percent: 0,
          complete: false,
          objectives: [],
          has_topic_lesson: true,
          subtopics: [
            {
              id: "st-1-uuid",
              title: "Place value",
              slug: "place-value",
              sequence: 1,
              has_lesson: true,
              lesson_completed: false,
              progress_percent: 0,
              quiz: null,
            },
          ],
          overall_quiz: {
            id: "quiz-1-uuid",
            title: "Numbers mastery",
            scope: "topic_mastery",
            available: true,
            unlocked: true,
            locked_reason: null,
            pass_threshold_percent: 70,
            attempt_count: 0,
            best_score_percent: null,
            passed: false,
            in_progress_attempt_id: null,
            recent_attempts: [],
          },
        },
        {
          id: "topic-2-uuid",
          title: "Decimals",
          slug: "decimals",
          sequence: 2,
          progress_percent: 0,
          complete: false,
          objectives: [],
          has_topic_lesson: false,
          subtopics: [],
          overall_quiz: null,
        },
      ],
    },
  ],
};

describe("adaptLearningDirectory", () => {
  it("maps has_topic_lesson onto AdminTopic", () => {
    const grades = adaptLearningDirectory(directory);
    const topics = grades[0]?.subjects[0]?.topics ?? [];
    expect(topics[0]?.hasTopicLesson).toBe(true);
    expect(topics[0]?.masteryQuiz?.id).toBe("quiz-1-uuid");
    expect(topics[1]?.hasTopicLesson).toBe(false);
    expect(topics[1]?.masteryQuiz).toBeNull();
  });

  it("flattens chapter cards from published subtopic lessons and leaf topics", () => {
    const subject = adaptLearningDirectory(directory)[0]?.subjects[0];
    expect(subject).toBeDefined();
    expect(subjectUnitCards(subject!).map((card) => card.title)).toEqual([
      "Numbers",
      "Decimals",
    ]);
    expect(subjectTopicLessonTopics(subject!).map((topic) => topic.title)).toEqual(["Numbers"]);
    expect(adminTopicPath("grade-8", "subj-math-uuid", "topic-1-uuid", { published: true })).toBe(
      "/admin/materials/grades/grade-8/subjects/subj-math-uuid/topics/topic-1-uuid?tab=published",
    );
    expect(
      adminTopicPath("grade-8", "subj-math-uuid", "topic-1-uuid", {
        published: true,
        unitId: "st-1-uuid",
      }),
    ).toBe(
      "/admin/materials/grades/grade-8/subjects/subj-math-uuid/topics/topic-1-uuid?tab=published&unit=st-1-uuid",
    );
  });

  it("omits outline-only subtopics and lists one topic card instead", () => {
    const grades = adaptLearningDirectory({
      subjects: [
        {
          ...directory.subjects[0]!,
          topics: [
            {
              id: "topic-gen-uuid",
              title: "Fractions chapter",
              slug: "fractions-chapter",
              sequence: 1,
              progress_percent: 0,
              complete: false,
              objectives: [],
              has_topic_lesson: true,
              subtopics: [
                {
                  id: "st-outline-uuid",
                  title: "Like fractions",
                  slug: "like-fractions",
                  sequence: 1,
                  has_lesson: false,
                  lesson_completed: false,
                  progress_percent: 0,
                  quiz: null,
                },
                {
                  id: "st-outline-2-uuid",
                  title: "Unlike fractions",
                  slug: "unlike-fractions",
                  sequence: 2,
                  has_lesson: false,
                  lesson_completed: false,
                  progress_percent: 0,
                  quiz: null,
                },
              ],
              overall_quiz: null,
            },
          ],
        },
      ],
    });
    const subject = grades[0]?.subjects[0];
    expect(subject).toBeDefined();
    expect(subjectUnitCards(subject!).map((card) => ({ title: card.title, unitId: card.unitId }))).toEqual(
      [{ title: "Fractions chapter", unitId: null }],
    );
    expect(subjectTopicLessonTopics(subject!)).toEqual([]);
  });

  it("keeps seeded subtopic lessons and skips sibling outline-only nodes", () => {
    const grades = adaptLearningDirectory({
      subjects: [
        {
          ...directory.subjects[0]!,
          topics: [
            {
              ...directory.subjects[0]!.topics[0]!,
              subtopics: [
                directory.subjects[0]!.topics[0]!.subtopics[0]!,
                {
                  id: "st-outline-uuid",
                  title: "Like fractions",
                  slug: "like-fractions",
                  sequence: 2,
                  has_lesson: false,
                  lesson_completed: false,
                  progress_percent: 0,
                  quiz: null,
                },
              ],
            },
          ],
        },
      ],
    });
    const subject = grades[0]?.subjects[0];
    expect(subjectUnitCards(subject!).map((card) => card.title)).toEqual(["Numbers"]);
    expect(subjectTopicLessonTopics(subject!).map((topic) => topic.title)).toEqual(["Numbers"]);
  });
});
