/** Static fixtures for the teacher mock workspace (subject teaching assignments only). */

export type RosterStatus = "active" | "inactive" | "transferred";

export type RosterStudent = {
  id: string;
  fullName: string;
  rollNo: string;
  status: RosterStatus;
};

export type LessonItem = {
  id: string;
  title: string;
  status: "published" | "draft";
};

export type QuizItem = {
  id: string;
  title: string;
  kind: "subtopic" | "topic";
  status: "published" | "draft";
};

export type TopicMaterial = {
  id: string;
  title: string;
  lessons: LessonItem[];
  quizzes: QuizItem[];
};

export type ProgressStrip = {
  /** Short label shown above the bar */
  label: string;
  /** 0–100 */
  pct: number;
  /** Supporting sentence under the bar */
  detail: string;
};

export type TeacherSubject = {
  id: string;
  name: string;
  code: string;
  blurb: string;
  topics: TopicMaterial[];
  progress: ProgressStrip;
};

export type TeacherSection = {
  id: string;
  gradeName: string;
  sectionCode: string;
  /** Display label e.g. "Grade 8-A" */
  label: string;
  academicPeriod: string;
  subjects: TeacherSubject[];
  roster: RosterStudent[];
};

const ROSTER_8A: RosterStudent[] = [
  { id: "stu-8a-01", fullName: "Aanya Mehra", rollNo: "08A-01", status: "active" },
  { id: "stu-8a-02", fullName: "Dev Patel", rollNo: "08A-02", status: "active" },
  { id: "stu-8a-03", fullName: "Isha Reddy", rollNo: "08A-03", status: "active" },
  { id: "stu-8a-04", fullName: "Kabir Singh", rollNo: "08A-04", status: "active" },
  { id: "stu-8a-05", fullName: "Maya Krishnan", rollNo: "08A-05", status: "inactive" },
  { id: "stu-8a-06", fullName: "Rohan Das", rollNo: "08A-06", status: "active" },
];

const ROSTER_8B: RosterStudent[] = [
  { id: "stu-8b-01", fullName: "Anika Bose", rollNo: "08B-01", status: "active" },
  { id: "stu-8b-02", fullName: "Farhan Ali", rollNo: "08B-02", status: "active" },
  { id: "stu-8b-03", fullName: "Leela Nair", rollNo: "08B-03", status: "active" },
  { id: "stu-8b-04", fullName: "Samar Joshi", rollNo: "08B-04", status: "transferred" },
  { id: "stu-8b-05", fullName: "Zara Khan", rollNo: "08B-05", status: "active" },
];

const ROSTER_9A: RosterStudent[] = [
  { id: "stu-9a-01", fullName: "Arjun Rao", rollNo: "09A-01", status: "active" },
  { id: "stu-9a-02", fullName: "Diya Shah", rollNo: "09A-02", status: "active" },
  { id: "stu-9a-03", fullName: "Harsh Malhotra", rollNo: "09A-03", status: "active" },
  { id: "stu-9a-04", fullName: "Nina Verghese", rollNo: "09A-04", status: "active" },
  { id: "stu-9a-05", fullName: "Omar Siddiqui", rollNo: "09A-05", status: "inactive" },
];

const MATH_TOPICS_G8: TopicMaterial[] = [
  {
    id: "topic-math-integers",
    title: "Integers & rational numbers",
    lessons: [
      { id: "les-int-1", title: "Number line & ordering", status: "published" },
      { id: "les-int-2", title: "Operations with integers", status: "published" },
      { id: "les-int-3", title: "Rational number form", status: "draft" },
    ],
    quizzes: [
      { id: "qz-int-sub", title: "Integers check-in", kind: "subtopic", status: "published" },
      { id: "qz-int-topic", title: "Topic mastery · Integers", kind: "topic", status: "published" },
    ],
  },
  {
    id: "topic-math-algebra",
    title: "Algebra foundations",
    lessons: [
      { id: "les-alg-1", title: "Variables & expressions", status: "published" },
      { id: "les-alg-2", title: "Simple linear equations", status: "published" },
    ],
    quizzes: [
      { id: "qz-alg-sub", title: "Expressions practice", kind: "subtopic", status: "published" },
      { id: "qz-alg-topic", title: "Topic mastery · Algebra", kind: "topic", status: "draft" },
    ],
  },
];

const SCIENCE_TOPICS_G9: TopicMaterial[] = [
  {
    id: "topic-sci-matter",
    title: "Matter around us",
    lessons: [
      { id: "les-mat-1", title: "States of matter", status: "published" },
      { id: "les-mat-2", title: "Mixtures & separation", status: "published" },
      { id: "les-mat-3", title: "Atoms & molecules intro", status: "draft" },
    ],
    quizzes: [
      { id: "qz-mat-sub", title: "States quiz", kind: "subtopic", status: "published" },
      { id: "qz-mat-topic", title: "Topic mastery · Matter", kind: "topic", status: "published" },
    ],
  },
  {
    id: "topic-sci-living",
    title: "The living world",
    lessons: [
      { id: "les-liv-1", title: "Cell structure", status: "published" },
      { id: "les-liv-2", title: "Tissues overview", status: "published" },
    ],
    quizzes: [
      { id: "qz-liv-sub", title: "Cell check-in", kind: "subtopic", status: "published" },
      { id: "qz-liv-topic", title: "Topic mastery · Living world", kind: "topic", status: "draft" },
    ],
  },
];

/** Sections where this teacher has a subject teaching assignment. */
export const TEACHER_SECTIONS: TeacherSection[] = [
  {
    id: "sec-8a",
    gradeName: "Grade 8",
    sectionCode: "A",
    label: "Grade 8-A",
    academicPeriod: "2025–26",
    roster: ROSTER_8A,
    subjects: [
      {
        id: "subj-8a-math",
        name: "Mathematics",
        code: "MATH",
        blurb: "Number sense, algebra foundations, and geometry basics.",
        topics: MATH_TOPICS_G8,
        progress: {
          label: "Class progress · Mathematics",
          pct: 62,
          detail: "4 of 5 published lessons started · Integers topic nearly complete",
        },
      },
    ],
  },
  {
    id: "sec-8b",
    gradeName: "Grade 8",
    sectionCode: "B",
    label: "Grade 8-B",
    academicPeriod: "2025–26",
    roster: ROSTER_8B,
    subjects: [
      {
        id: "subj-8b-math",
        name: "Mathematics",
        code: "MATH",
        blurb: "Same Grade 8 maths sequence for section B.",
        topics: MATH_TOPICS_G8,
        progress: {
          label: "Class progress · Mathematics",
          pct: 48,
          detail: "Algebra foundations in progress · 3 of 5 students past integers mastery",
        },
      },
    ],
  },
  {
    id: "sec-9a",
    gradeName: "Grade 9",
    sectionCode: "A",
    label: "Grade 9-A",
    academicPeriod: "2025–26",
    roster: ROSTER_9A,
    subjects: [
      {
        id: "subj-9a-science",
        name: "Science",
        code: "SCI",
        blurb: "Matter, living systems, and introductory lab ideas.",
        topics: SCIENCE_TOPICS_G9,
        progress: {
          label: "Class progress · Science",
          pct: 35,
          detail: "Matter topic underway · Living world not started for most students",
        },
      },
    ],
  },
];

export function getTeacherSection(sectionId: string): TeacherSection | undefined {
  return TEACHER_SECTIONS.find((s) => s.id === sectionId);
}

export function getTeacherSubject(
  sectionId: string,
  subjectId: string,
): { section: TeacherSection; subject: TeacherSubject } | undefined {
  const section = getTeacherSection(sectionId);
  if (!section) return undefined;
  const subject = section.subjects.find((s) => s.id === subjectId);
  if (!subject) return undefined;
  return { section, subject };
}

export function rosterStatusLabel(status: RosterStatus): string {
  switch (status) {
    case "active":
      return "Active";
    case "inactive":
      return "Inactive";
    case "transferred":
      return "Transferred";
  }
}
