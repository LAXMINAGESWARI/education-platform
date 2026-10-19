import { useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { getSubtopicMaterial, getSubtopicQuiz, getTopicMaterial } from "../../api/materials";
import type { LessonMaterial, QuizMaterial, QuizSummary, TopicNode } from "../../api/types";
import { Crumbs } from "../../components/Crumbs";
import { MarkdownContent } from "../../components/MarkdownContent";
import { useLearningDirectory } from "../../lib/useLearningDirectory";
import { findClass, useTeacherClasses } from "../../lib/useTeacherClasses";

type Tab = "lessons" | "quizzes";

type OpenTopicLesson = { kind: "topic-lesson"; topicId: string };
type OpenLesson = { kind: "lesson"; subtopicId: string };
type OpenQuiz = { kind: "quiz"; subtopicId: string };
type OpenItem = OpenTopicLesson | OpenLesson | OpenQuiz;

export function SubjectMaterialsPage() {
  const { sectionId = "", subjectId = "" } = useParams();
  const { loading: classesLoading, error: classesError, classes } = useTeacherClasses();
  const { directory, loading: directoryLoading, error: directoryError } = useLearningDirectory();
  const [tab, setTab] = useState<Tab>("lessons");
  const [openTopicId, setOpenTopicId] = useState<string | null>(null);
  const [openItem, setOpenItem] = useState<OpenItem | null>(null);
  const [lesson, setLesson] = useState<LessonMaterial | null>(null);
  const [quiz, setQuiz] = useState<QuizMaterial | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const entry = findClass(classes, sectionId);
  const subject = directory?.subjects.find(
    (row) => row.id === subjectId && row.grade_name === entry?.gradeName,
  );

  const activeTopicId = openTopicId ?? subject?.topics[0]?.id ?? null;
  const activeTopic = subject?.topics.find((topic) => topic.id === activeTopicId) ?? null;

  if (classesLoading || directoryLoading) {
    return <div className="banner banner--info">Loading…</div>;
  }
  if (classesError) {
    return (
      <div className="banner banner--warning" role="alert">
        {classesError}
      </div>
    );
  }
  if (!entry) {
    return <Navigate to="/teacher" replace />;
  }
  if (directoryError) {
    return (
      <div className="banner banner--warning" role="alert">
        {directoryError}
      </div>
    );
  }
  if (!subject) {
    return <Navigate to={`/teacher/classes/${entry.id}`} replace />;
  }

  const sectionLabel = `${entry.gradeName} · ${entry.sectionName}`;

  async function openTopicLesson(topicId: string) {
    setOpenItem({ kind: "topic-lesson", topicId });
    setPreviewError(null);
    setQuiz(null);
    setPreviewLoading(true);
    try {
      setLesson(await getTopicMaterial(topicId));
    } catch (err: unknown) {
      setLesson(null);
      setPreviewError(err instanceof Error ? err.message : "Could not load the lesson.");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function openLesson(subtopicId: string) {
    setOpenItem({ kind: "lesson", subtopicId });
    setPreviewError(null);
    setQuiz(null);
    setPreviewLoading(true);
    try {
      setLesson(await getSubtopicMaterial(subtopicId));
    } catch (err: unknown) {
      setLesson(null);
      setPreviewError(err instanceof Error ? err.message : "Could not load the lesson.");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function openQuiz(subtopicId: string) {
    setOpenItem({ kind: "quiz", subtopicId });
    setPreviewError(null);
    setLesson(null);
    setPreviewLoading(true);
    try {
      setQuiz(await getSubtopicQuiz(subtopicId));
    } catch (err: unknown) {
      setQuiz(null);
      setPreviewError(err instanceof Error ? err.message : "Could not load the quiz.");
    } finally {
      setPreviewLoading(false);
    }
  }

  return (
    <>
      <Crumbs
        parts={[
          { label: "My classes", to: "/teacher" },
          { label: sectionLabel, to: `/teacher/classes/${entry.id}` },
          { label: subject.name },
        ]}
      />
      <header className="page-head">
        <p className="kicker">
          {sectionLabel} · {subject.code}
        </p>
        <h1>{subject.name}</h1>
        <p>
          Published lessons and released quiz questions for this offering. Read-only — no
          attempts.
        </p>
      </header>

      <section className="teacher-progress-strip" aria-labelledby="teacher-progress-heading">
        <div className="teacher-progress-strip__head">
          <h2 id="teacher-progress-heading">Class progress</h2>
          <span className="teacher-progress-strip__pct">{subject.progress_percent}%</span>
        </div>
        <div
          className={`progress ${subject.progress_percent >= 100 ? "progress--complete" : "progress--in-progress"}`}
          aria-hidden="true"
        >
          <span style={{ width: `${subject.progress_percent}%` }} />
        </div>
        <p className="teacher-progress-strip__detail">
          {subject.grade_name} · {subject.academic_period_name}
        </p>
      </section>

      <div className="teacher-materials">
        <aside className="teacher-materials__topics" aria-label="Topics">
          <h2 className="section-title">Topics</h2>
          <ul className="teacher-topic-list">
            {subject.topics.map((topic) => {
              const isActive = topic.id === activeTopicId;
              const lessonCount = lessonCountFor(topic);
              const quizCount = quizCountFor(topic);
              return (
                <li key={topic.id}>
                  <button
                    type="button"
                    className={`teacher-topic-list__btn${isActive ? " is-active" : ""}`}
                    aria-current={isActive ? "true" : undefined}
                    onClick={() => {
                      setOpenTopicId(topic.id);
                      setOpenItem(null);
                      setLesson(null);
                      setQuiz(null);
                      setPreviewError(null);
                    }}
                  >
                    <span>{topic.title}</span>
                    <span className="teacher-topic-list__meta">
                      {countPhrase(lessonCount, "lesson", "lessons")} ·{" "}
                      {countPhrase(quizCount, "quiz", "quizzes")}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        <div className="teacher-materials__detail">
          {activeTopic ? (
            <>
              <div className="teacher-materials__tabs" role="tablist" aria-label="Material type">
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === "lessons"}
                  className={`teacher-tab${tab === "lessons" ? " is-active" : ""}`}
                  onClick={() => setTab("lessons")}
                >
                  Lessons
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === "quizzes"}
                  className={`teacher-tab${tab === "quizzes" ? " is-active" : ""}`}
                  onClick={() => setTab("quizzes")}
                >
                  Quizzes
                </button>
              </div>

              <h3 className="teacher-materials__topic-title">{activeTopic.title}</h3>
              <p>
                <Link to={`/teacher/topics/${activeTopic.id}/generation`}>
                  Review generation
                </Link>
              </p>
              <TabBody
                tab={tab}
                topic={activeTopic}
                openItem={openItem}
                onOpenTopicLesson={(id) => void openTopicLesson(id)}
                onOpenLesson={(id) => void openLesson(id)}
                onOpenQuiz={(id) => void openQuiz(id)}
              />
              {previewLoading && (
                <p className="muted" role="status">
                  Loading…
                </p>
              )}
              {previewError && (
                <div className="banner banner--warning" role="alert">
                  {previewError}
                </div>
              )}
              {lesson && (openItem?.kind === "lesson" || openItem?.kind === "topic-lesson") && (
                <LessonPreview lesson={lesson} />
              )}
              {quiz && openItem?.kind === "quiz" && <QuizPreview quiz={quiz} />}
            </>
          ) : (
            <div className="alert alert--info">No topics published for this subject yet.</div>
          )}
        </div>
      </div>
    </>
  );
}

function lessonCountFor(topic: TopicNode): number {
  const topicLesson = topic.has_topic_lesson ? 1 : 0;
  const subtopicLessons = topic.subtopics.filter((subtopic) => subtopic.has_lesson).length;
  return topicLesson + subtopicLessons;
}

function quizCountFor(topic: TopicNode): number {
  return (
    topic.subtopics.filter((subtopic) => subtopic.quiz?.available).length +
    (topic.overall_quiz?.available ? 1 : 0)
  );
}

function countPhrase(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function TabBody({
  tab,
  topic,
  openItem,
  onOpenTopicLesson,
  onOpenLesson,
  onOpenQuiz,
}: {
  tab: Tab;
  topic: TopicNode;
  openItem: OpenItem | null;
  onOpenTopicLesson: (topicId: string) => void;
  onOpenLesson: (subtopicId: string) => void;
  onOpenQuiz: (subtopicId: string) => void;
}) {
  switch (tab) {
    case "lessons": {
      const publishedSubtopics = topic.subtopics.filter((subtopic) => subtopic.has_lesson);
      if (!topic.has_topic_lesson && publishedSubtopics.length === 0) {
        return <p className="muted">No published lesson for this topic.</p>;
      }
      return (
        <ul className="teacher-item-list">
          {topic.has_topic_lesson && (
            <li>
              <button
                type="button"
                className={`teacher-item-list__row${
                  openItem?.kind === "topic-lesson" && openItem.topicId === topic.id
                    ? " is-active"
                    : ""
                }`}
                onClick={() => onOpenTopicLesson(topic.id)}
              >
                <span>{topic.title} lesson</span>
                <span className="badge badge--ok">Published</span>
              </button>
            </li>
          )}
          {publishedSubtopics.map((subtopic) => {
            const selected = openItem?.kind === "lesson" && openItem.subtopicId === subtopic.id;
            return (
              <li key={subtopic.id}>
                <button
                  type="button"
                  className={`teacher-item-list__row${selected ? " is-active" : ""}`}
                  onClick={() => onOpenLesson(subtopic.id)}
                >
                  <span>{subtopic.title}</span>
                  <span className="badge badge--ok">Published</span>
                </button>
              </li>
            );
          })}
        </ul>
      );
    }
    case "quizzes":
      return (
        <ul className="teacher-item-list">
          {topic.subtopics.map((subtopic) => {
            if (!subtopic.quiz?.available || !subtopic.quiz.id) return null;
            const selected = openItem?.kind === "quiz" && openItem.subtopicId === subtopic.id;
            return (
              <li key={subtopic.quiz.id}>
                <button
                  type="button"
                  className={`teacher-item-list__row${selected ? " is-active" : ""}`}
                  onClick={() => onOpenQuiz(subtopic.id)}
                >
                  <span>
                    {subtopic.quiz.title ?? subtopic.title}
                    <span className="teacher-item-list__kind">Subtopic</span>
                  </span>
                  <span className="badge badge--ok">Released</span>
                </button>
              </li>
            );
          })}
          {topic.overall_quiz?.available && topic.overall_quiz.id && (
            <li>
              <TopicMasteryRow quiz={topic.overall_quiz} />
            </li>
          )}
        </ul>
      );
    default: {
      const _exhaustive: never = tab;
      return _exhaustive;
    }
  }
}

function TopicMasteryRow({ quiz }: { quiz: QuizSummary }) {
  return (
    <div className="teacher-item-list__row">
      <span>
        {quiz.title ?? "Topic mastery"}
        <span className="teacher-item-list__kind">Topic mastery · listed only</span>
      </span>
      <span className="badge badge--ok">Released</span>
    </div>
  );
}

function LessonPreview({ lesson }: { lesson: LessonMaterial }) {
  return (
    <article className="teacher-preview" aria-label="Lesson preview">
      <h4>{lesson.title}</h4>
      <p className="muted">Read-only preview. Student progress is not recorded here.</p>
      {lesson.slides.map((slide) => (
        <section key={slide.number} className="teacher-preview__slide">
          <h5>
            Slide {slide.number}. {slide.title}
          </h5>
          <div className="markdown">
            <MarkdownContent>{slide.content}</MarkdownContent>
          </div>
        </section>
      ))}
    </article>
  );
}

function QuizPreview({ quiz }: { quiz: QuizMaterial }) {
  return (
    <article className="teacher-preview" aria-label="Quiz preview">
      <h4>{quiz.title}</h4>
      <p className="muted">
        Released questions only — no answer keys, and this does not start an attempt.
      </p>
      <ol className="teacher-preview__questions">
        {quiz.questions.map((question) => (
          <li key={question.number}>
            <p>
              <strong>Q{question.number}.</strong> {question.prompt}
            </p>
            <ul className="draft-options">
              {question.options.map((option) => (
                <li key={option.label} className="draft-option">
                  <span className="draft-option__label">{option.label}</span>
                  <span>{option.text}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </article>
  );
}
