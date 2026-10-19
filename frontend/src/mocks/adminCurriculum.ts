/** Fixture curriculum for the admin materials browser (mock only). */

export type PublishStatus = "published" | "draft";

export type AdminQuiz = {
  id: string;
  title: string;
  kind: "subtopic" | "topic_mastery";
  status: PublishStatus;
  questionCount: number;
};

export type AdminLesson = {
  id: string;
  title: string;
  status: PublishStatus;
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
  /** Student-facing label for Topic. */
  unitLabel: string;
  order: number;
  status: PublishStatus;
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
  id: string;
  number: number;
  name: string;
  subjects: AdminSubject[];
};

function subtopic(
  grade: number,
  subjectCode: string,
  topicOrd: number,
  ord: number,
  title: string,
  lessonStatus: PublishStatus,
  quizStatus: PublishStatus | null,
): AdminSubtopic {
  const base = `g${grade}-${subjectCode.toLowerCase()}-t${topicOrd}-s${ord}`;
  return {
    id: base,
    title,
    order: ord,
    lesson: {
      id: `${base}-lesson`,
      title: `${title} · lesson`,
      status: lessonStatus,
    },
    quiz:
      quizStatus === null
        ? null
        : {
            id: `${base}-quiz`,
            title: `${title} · check`,
            kind: "subtopic",
            status: quizStatus,
            questionCount: 5 + (ord % 3),
          },
  };
}

function topic(
  grade: number,
  subjectCode: string,
  ord: number,
  title: string,
  status: PublishStatus,
  subtopics: AdminSubtopic[],
  masteryStatus: PublishStatus | null,
): AdminTopic {
  const id = `g${grade}-${subjectCode.toLowerCase()}-t${ord}`;
  return {
    id,
    title,
    unitLabel: `Topic ${ord}`,
    order: ord,
    status,
    subtopics,
    masteryQuiz:
      masteryStatus === null
        ? null
        : {
            id: `${id}-mastery`,
            title: `${title} · mastery`,
            kind: "topic_mastery",
            status: masteryStatus,
            questionCount: 10,
          },
  };
}

function mathForGrade(n: number): AdminSubject {
  const code = "MATH";
  return {
    id: `g${n}-math`,
    name: "Mathematics",
    code,
    blurb: "Number sense, algebra foundations, and geometry basics.",
    topics: [
      topic(
        n,
        code,
        1,
        "Numbers and operations",
        "published",
        [
          subtopic(n, code, 1, 1, "Place value", "published", "published"),
          subtopic(n, code, 1, 2, "Addition and subtraction", "published", "published"),
          subtopic(n, code, 1, 3, "Multiplication facts", "draft", "draft"),
        ],
        "published",
      ),
      topic(
        n,
        code,
        2,
        "Fractions and decimals",
        n >= 5 ? "published" : "draft",
        [
          subtopic(n, code, 2, 1, "Parts of a whole", "published", "published"),
          subtopic(n, code, 2, 2, "Comparing fractions", n >= 6 ? "published" : "draft", null),
        ],
        n >= 6 ? "published" : "draft",
      ),
      topic(
        n,
        code,
        3,
        "Geometry basics",
        "draft",
        [
          subtopic(n, code, 3, 1, "Shapes and angles", "draft", "draft"),
          subtopic(n, code, 3, 2, "Perimeter and area", "draft", null),
        ],
        null,
      ),
    ],
  };
}

function scienceForGrade(n: number): AdminSubject {
  const code = "SCI";
  return {
    id: `g${n}-sci`,
    name: "Science",
    code,
    blurb: "Living world, matter, and energy.",
    topics: [
      topic(
        n,
        code,
        1,
        "Living things",
        "published",
        [
          subtopic(n, code, 1, 1, "Cells and organisms", "published", "published"),
          subtopic(n, code, 1, 2, "Habitats", "published", "draft"),
        ],
        "published",
      ),
      topic(
        n,
        code,
        2,
        "Matter and materials",
        n >= 7 ? "published" : "draft",
        [
          subtopic(n, code, 2, 1, "States of matter", "published", "published"),
          subtopic(n, code, 2, 2, "Mixtures", "draft", null),
        ],
        n >= 8 ? "draft" : null,
      ),
    ],
  };
}

function englishForGrade(n: number): AdminSubject {
  const code = "ENG";
  return {
    id: `g${n}-eng`,
    name: "English",
    code,
    blurb: "Reading comprehension and writing craft.",
    topics: [
      topic(
        n,
        code,
        1,
        "Reading workshop",
        "published",
        [
          subtopic(n, code, 1, 1, "Finding the main idea", "published", "published"),
          subtopic(n, code, 1, 2, "Inference", "published", "published"),
        ],
        "published",
      ),
      topic(
        n,
        code,
        2,
        "Writing craft",
        "draft",
        [
          subtopic(n, code, 2, 1, "Paragraph structure", "draft", "draft"),
          subtopic(n, code, 2, 2, "Revision habits", "draft", null),
        ],
        null,
      ),
    ],
  };
}

function socialForGrade(n: number): AdminSubject | null {
  if (n < 4) return null;
  const code = "SST";
  return {
    id: `g${n}-sst`,
    name: "Social Studies",
    code,
    blurb: "Civics, geography, and local history.",
    topics: [
      topic(
        n,
        code,
        1,
        "Our community",
        "published",
        [
          subtopic(n, code, 1, 1, "Maps and places", "published", "published"),
          subtopic(n, code, 1, 2, "Local government", n >= 6 ? "published" : "draft", "draft"),
        ],
        "published",
      ),
    ],
  };
}

function subjectsForGrade(n: number): AdminSubject[] {
  const list: AdminSubject[] = [mathForGrade(n), englishForGrade(n), scienceForGrade(n)];
  const social = socialForGrade(n);
  if (social) list.push(social);
  return list;
}

export const ADMIN_GRADES: AdminGrade[] = Array.from({ length: 10 }, (_, i) => {
  const number = i + 1;
  return {
    id: `grade-${number}`,
    number,
    name: `Grade ${number}`,
    subjects: subjectsForGrade(number),
  };
});

export function getAdminGrade(gradeId: string): AdminGrade | undefined {
  return ADMIN_GRADES.find((g) => g.id === gradeId);
}

export function getAdminSubject(
  gradeId: string,
  subjectId: string,
): { grade: AdminGrade; subject: AdminSubject } | undefined {
  const grade = getAdminGrade(gradeId);
  if (!grade) return undefined;
  const subject = grade.subjects.find((s) => s.id === subjectId);
  if (!subject) return undefined;
  return { grade, subject };
}

export function getAdminTopic(
  gradeId: string,
  subjectId: string,
  topicId: string,
): { grade: AdminGrade; subject: AdminSubject; topic: AdminTopic } | undefined {
  const found = getAdminSubject(gradeId, subjectId);
  if (!found) return undefined;
  const topic = found.subject.topics.find((t) => t.id === topicId);
  if (!topic) return undefined;
  return { ...found, topic };
}

export function countPublishedTopics(subject: AdminSubject): number {
  return subject.topics.filter((t) => t.status === "published").length;
}

export function gradePublishSummary(grade: AdminGrade): {
  subjects: number;
  publishedTopics: number;
  draftTopics: number;
} {
  let publishedTopics = 0;
  let draftTopics = 0;
  for (const subject of grade.subjects) {
    for (const t of subject.topics) {
      if (t.status === "published") publishedTopics += 1;
      else draftTopics += 1;
    }
  }
  return {
    subjects: grade.subjects.length,
    publishedTopics,
    draftTopics,
  };
}
