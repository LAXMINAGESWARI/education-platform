import { describe, expect, it } from "vitest";

import type { LearningDirectory } from "../api/types";
import {
  resolveLearningPath,
  resolvePathFromQuizId,
  learningPathCrumb,
} from "./learningPath";

const directory: LearningDirectory = {
  subjects: [
    {
      id: "subj-1",
      code: "MATH",
      name: "Mathematics",
      grade_name: "Grade 8",
      academic_period_name: "2026-27",
      progress_percent: 40,
      topics: [
        {
          id: "topic-1",
          title: "Approved Materials",
          slug: "approved_materials",
          sequence: 1,
          progress_percent: 40,
          complete: false,
          objectives: [],
          has_topic_lesson: true,
          topic_lesson_completed: false,
          topic_source_material_version_id: "ver-topic-1",
          overall_quiz: {
            id: "overall-1",
            title: "Overall",
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
          subtopics: [
            {
              id: "st-1",
              title: "Properties of Rectangles and Squares",
              slug: "rectangles_squares_properties",
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
                attempt_count: 0,
                best_score_percent: null,
                passed: false,
                in_progress_attempt_id: null,
                recent_attempts: [],
              },
            },
          ],
        },
      ],
    },
  ],
};

describe("learningPath", () => {
  it("points topic mastery at the published topic lesson", () => {
    const path = resolveLearningPath(directory, "topic_mastery", "topic-1");
    expect(path).toMatchObject({
      lessonPath: "/subjects/subj-1/topics/topic-1/lesson",
      slidesPath: "/subjects/subj-1/topics/topic-1/lesson",
      hasTopicLesson: true,
      overallUnlocked: true,
    });
    expect(learningPathCrumb(path!)).toEqual({
      label: "Approved Materials",
      to: "/subjects/subj-1/topics/topic-1",
    });
    expect(resolvePathFromQuizId(directory, "overall-1")?.lessonPath).toBe(
      "/subjects/subj-1/topics/topic-1/lesson",
    );
  });

  it("keeps seeded subtopic lesson routes", () => {
    const path = resolveLearningPath(directory, "subtopic_mastery", "st-1");
    expect(path).toMatchObject({
      lessonPath: "/subjects/subj-1/subtopics/st-1/lesson",
      slidesPath: "/subjects/subj-1/subtopics/st-1/lesson/slides",
      hasTopicLesson: true,
      subtopicTitle: "Properties of Rectangles and Squares",
    });
    expect(learningPathCrumb(path!)).toEqual({
      label: "Properties of Rectangles and Squares",
      to: "/subjects/subj-1/subtopics/st-1/lesson",
    });
  });

  it("does not invent a topic lesson path when none is published", () => {
    const withoutLesson: LearningDirectory = {
      subjects: [
        {
          ...directory.subjects[0],
          topics: [
            {
              ...directory.subjects[0].topics[0],
              has_topic_lesson: false,
              topic_source_material_version_id: null,
              overall_quiz: {
                ...directory.subjects[0].topics[0].overall_quiz!,
                unlocked: false,
                locked_reason: "Pass all subtopic quizzes first",
              },
            },
          ],
        },
      ],
    };
    const path = resolveLearningPath(withoutLesson, "topic_mastery", "topic-1");
    expect(path?.lessonPath).toBeNull();
    expect(path?.hasTopicLesson).toBe(false);
    expect(learningPathCrumb(path!)).toBeNull();
  });
});
