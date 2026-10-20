import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";

import { listTopicGenerationRuns } from "../../api/generation";
import { getSubtopicMaterial, getTopicMaterial } from "../../api/materials";
import type { GenerationQaItem, GenerationRun, LessonMaterial } from "../../api/types";
import { Crumbs, type Crumb } from "../../components/Crumbs";
import { LessonSlideStage } from "../../components/LessonSlideStage";
import { QuizItemCards } from "../../components/QuizItemCards";
import { TopicGenerationUpload } from "../../components/TopicGenerationUpload";
import { TopicItemStats } from "../../components/TopicItemStats";
import { slidesForLessonView } from "../../lib/lessonSlides";
import { topicLessonPath } from "../../lib/subjectMaterial";
import { useAdminDirectory } from "../../lib/useAdminDirectory";

type TabId = "upload" | "published";

const PUBLISHED_MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

async function loadPublishedLesson(
  topicId: string,
  subtopicId: string | null,
  preferSubtopic: boolean,
): Promise<LessonMaterial> {
  if (preferSubtopic && subtopicId) {
    try {
      return await getSubtopicMaterial(subtopicId);
    } catch {
      return await getTopicMaterial(topicId);
    }
  }
  try {
    return await getTopicMaterial(topicId);
  } catch (err) {
    if (subtopicId) return await getSubtopicMaterial(subtopicId);
    throw err;
  }
}

function formatPublishedDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const month = PUBLISHED_MONTHS[date.getUTCMonth()];
  if (!month) return null;
  return `${date.getUTCDate()} ${month} ${date.getUTCFullYear()}`;
}

function studentLessonHref(
  subjectId: string,
  topicId: string,
  subtopicId: string | null,
  preferSubtopic: boolean,
): string {
  if (preferSubtopic && subtopicId) {
    return `/subjects/${subjectId}/subtopics/${subtopicId}/lesson`;
  }
  return topicLessonPath(subjectId, topicId);
}

function difficultyMix(items: GenerationQaItem[]): string {
  const counts = { easy: 0, medium: 0, hard: 0, unlabeled: 0 };
  for (const item of items) {
    switch (item.difficulty) {
      case "easy":
        counts.easy += 1;
        break;
      case "medium":
        counts.medium += 1;
        break;
      case "hard":
        counts.hard += 1;
        break;
      default:
        counts.unlabeled += 1;
        break;
    }
  }
  const parts: string[] = [];
  if (counts.easy > 0) parts.push(`${counts.easy} easy`);
  if (counts.medium > 0) parts.push(`${counts.medium} medium`);
  if (counts.hard > 0) parts.push(`${counts.hard} hard`);
  if (counts.unlabeled > 0 && parts.length > 0) parts.push(`${counts.unlabeled} unlabeled`);
  return parts.join(" · ");
}

function AdminPublishedQuiz({
  items,
  loading,
}: {
  items: GenerationQaItem[];
  loading: boolean;
}) {
  const [difficulty, setDifficulty] = useState("all");
  const [subtopic, setSubtopic] = useState("all");
  const sorted = useMemo(
    () => [...items].sort((left, right) => left.sequence - right.sequence),
    [items],
  );
  const difficulties = useMemo(() => {
    const values = new Set<string>();
    for (const item of sorted) {
      if (item.difficulty) values.add(item.difficulty);
    }
    return [...values].sort();
  }, [sorted]);
  const subtopics = useMemo(() => {
    const values = new Set<string>();
    for (const item of sorted) {
      if (item.subtopic_title) values.add(item.subtopic_title);
    }
    return [...values].sort((left, right) => left.localeCompare(right));
  }, [sorted]);
  const filtered = useMemo(
    () =>
      sorted.filter((item) => {
        if (difficulty !== "all" && item.difficulty !== difficulty) return false;
        if (subtopic !== "all" && item.subtopic_title !== subtopic) return false;
        return true;
      }),
    [sorted, difficulty, subtopic],
  );
  const countLabel = sorted.length === 1 ? "1 question" : `${sorted.length} questions`;
  const mix = difficultyMix(sorted);

  return (
    <div className="lesson-quiz-panel">
      <div className="lesson-quiz-panel__hero">
        <div>
          <h2 className="lesson-quiz-panel__title">Topic quiz</h2>
          <div className="lesson-quiz-panel__meta">
            <span className="badge badge--ok">Published</span>
            <span className="lesson-toolbar__hint">
              {loading
                ? "Loading quiz…"
                : sorted.length > 0
                  ? mix
                    ? `${countLabel} · ${mix}`
                    : `${countLabel} · read-only`
                  : "No quiz linked"}
            </span>
          </div>
        </div>
      </div>
      {loading ? (
        <p className="lesson-toolbar__hint" role="status">
          Loading published quiz…
        </p>
      ) : sorted.length === 0 ? (
        <p className="lesson-toolbar__hint">No published quiz items on the latest run.</p>
      ) : (
        <>
          {difficulties.length > 0 || subtopics.length > 0 ? (
            <div className="admin-published-filters">
              {difficulties.length > 0 ? (
                <label>
                  Difficulty
                  <select
                    aria-label="Filter by difficulty"
                    value={difficulty}
                    onChange={(event) => setDifficulty(event.target.value)}
                  >
                    <option value="all">All</option>
                    {difficulties.map((value) => (
                      <option key={value} value={value}>
                        {value.charAt(0).toUpperCase() + value.slice(1)}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              {subtopics.length > 0 ? (
                <label>
                  Subtopic
                  <select
                    aria-label="Filter by subtopic"
                    value={subtopic}
                    onChange={(event) => setSubtopic(event.target.value)}
                  >
                    <option value="all">All</option>
                    {subtopics.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
          ) : null}
          {filtered.length === 0 ? (
            <p className="lesson-toolbar__hint">No questions match this filter.</p>
          ) : (
            <QuizItemCards items={filtered} />
          )}
        </>
      )}
    </div>
  );
}

function AdminPublishedMaterial({
  subjectId,
  topicId,
  subtopicId,
  fallbackTitle,
  hasLesson,
  quizItems,
  quizLoading,
  preferSubtopic,
}: {
  subjectId: string;
  topicId: string;
  subtopicId: string | null;
  fallbackTitle: string;
  hasLesson: boolean;
  quizItems: GenerationQaItem[];
  quizLoading: boolean;
  preferSubtopic: boolean;
}) {
  const [lesson, setLesson] = useState<LessonMaterial | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [slideIndex, setSlideIndex] = useState(0);

  useEffect(() => {
    if (!hasLesson) {
      setLesson(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLesson(null);
    setError(null);
    void (async () => {
      try {
        const data = await loadPublishedLesson(topicId, subtopicId, preferSubtopic);
        if (!cancelled) setLesson(data);
      } catch {
        if (!cancelled) setError("Could not load the published lesson.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [topicId, subtopicId, hasLesson, preferSubtopic]);

  useEffect(() => {
    setSlideIndex(0);
  }, [lesson?.id, lesson?.source_material_version_id]);

  const slides = useMemo(
    () => (lesson ? slidesForLessonView({ ...lesson, title: lesson.title || fallbackTitle }) : []),
    [lesson, fallbackTitle],
  );
  const publishedOn = formatPublishedDate(lesson?.published_at);
  const slideLabel = slides.length === 1 ? "1 slide" : `${slides.length} slides`;
  const questionLabel =
    quizItems.length === 1 ? "1 question" : `${quizItems.length} questions`;
  const meta = [
    publishedOn ? `Published ${publishedOn}` : null,
    lesson ? slideLabel : null,
    quizLoading ? null : questionLabel,
  ].filter((part): part is string => part != null);
  const lessonHref = studentLessonHref(subjectId, topicId, subtopicId, preferSubtopic);
  const quizHref = lesson?.quiz_id ? `/quizzes/${lesson.quiz_id}` : null;

  let main: ReactNode;
  if (error) {
    main = (
      <p className="form__error" role="alert">
        {error}
      </p>
    );
  } else if (hasLesson && !lesson) {
    main = (
      <p className="muted" role="status">
        Loading topic lesson…
      </p>
    );
  } else if (slides.length === 0) {
    main = (
      <p className="muted" role="status">
        No published topic lesson.
      </p>
    );
  } else {
    main = (
      <LessonSlideStage
        slides={slides}
        index={Math.min(slideIndex, slides.length - 1)}
        onIndexChange={setSlideIndex}
        ariaLabel="Published lesson"
      />
    );
  }

  return (
    <div className="lesson-view admin-published-material">
      <header className="admin-published-head">
        <div>
          <p className="kicker">Published lesson</p>
          <h1>{lesson?.title || fallbackTitle}</h1>
          {meta.length > 0 ? <p className="admin-published-head__meta">{meta.join(" · ")}</p> : null}
        </div>
        <div className="admin-published-head__actions">
          <Link className="btn btn--outline btn--sm" to={lessonHref}>
            Open lesson as student
          </Link>
          {quizHref ? (
            <Link className="btn btn--outline btn--sm" to={quizHref}>
              Open quiz as student
            </Link>
          ) : null}
        </div>
      </header>
      <div className="lesson-layout">
        <section className="lesson-layout__main" aria-label="Published lesson">
          {main}
        </section>
        <aside className="lesson-layout__aside" id="published-quiz" aria-label="Published quiz">
          <AdminPublishedQuiz items={quizItems} loading={quizLoading} />
          <TopicItemStats topicId={topicId} />
        </aside>
      </div>
    </div>
  );
}

export function AdminMaterialsTopicDetailPage() {
  const { gradeKey = "", subjectId = "", topicId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const { grades, loading, error, reload, getTopic } = useAdminDirectory();
  const found = getTopic(gradeKey, subjectId, topicId);
  const [tabOverride, setTabOverride] = useState<TabId | null>(null);
  const [publishedRun, setPublishedRun] = useState<GenerationRun | null>(null);
  const [publishedRunLoading, setPublishedRunLoading] = useState(false);

  const grade = found?.grade;
  const subject = found?.subject;
  const topic = found?.topic;
  const requestedTab = searchParams.get("tab");
  const unitId = searchParams.get("unit");
  const requestedUnit = topic?.subtopics.find((row) => row.id === unitId) ?? null;
  const tab: TabId =
    tabOverride ??
    (requestedTab === "published" || topic?.hasTopicLesson ? "published" : "upload");
  const hasPublishedLesson = Boolean(
    topic?.hasTopicLesson || topic?.subtopics.some((row) => row.lesson),
  );
  const lessonSubtopicId = requestedUnit?.id ?? topic?.subtopics.find((row) => row.lesson)?.id ?? null;
  const preferSubtopicLesson = requestedUnit != null;

  useEffect(() => {
    setTabOverride(null);
  }, [topicId, unitId, requestedTab]);

  useEffect(() => {
    if (!topic) {
      setPublishedRun(null);
      setPublishedRunLoading(false);
      return;
    }
    let cancelled = false;
    setPublishedRunLoading(true);
    void (async () => {
      try {
        const runs = await listTopicGenerationRuns(topic.id);
        if (cancelled) return;
        const latestPublished = runs.find((run) => run.phase === "published") ?? null;
        setPublishedRun(latestPublished);
      } catch {
        if (!cancelled) setPublishedRun(null);
      } finally {
        if (!cancelled) setPublishedRunLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [topic]);

  const crumbParts = useMemo(() => {
    const parts: Crumb[] = [
      { label: "Materials", to: "/admin/materials" },
      {
        label: grade?.name ?? "Grade",
        to: grade ? `/admin/materials/grades/${grade.key}` : undefined,
      },
      {
        label: subject?.name ?? "Subject",
        to:
          grade && subject
            ? `/admin/materials/grades/${grade.key}/subjects/${subject.id}`
            : undefined,
      },
      {
        label: topic?.title ?? "Topic",
      },
    ];
    return parts;
  }, [grade, subject, topic]);

  if (!loading && !error && grades && !found) {
    return <Navigate to="/admin/materials" replace />;
  }

  function openPublished() {
    setTabOverride("published");
  }

  function renderPublished() {
    if (!topic) return null;
    return (
      <AdminPublishedMaterial
        subjectId={subjectId}
        topicId={topic.id}
        subtopicId={lessonSubtopicId}
        fallbackTitle={requestedUnit?.title ?? topic.title}
        hasLesson={hasPublishedLesson}
        quizItems={publishedRun?.qa_items ?? []}
        quizLoading={publishedRunLoading}
        preferSubtopic={preferSubtopicLesson}
      />
    );
  }

  return (
    <div className="admin-materials admin-unit">
      <Crumbs parts={crumbParts} />

      {loading && (
        <p className="muted" role="status">
          Loading curriculum…
        </p>
      )}
      {error && (
        <p className="form__error" role="alert">
          {error}
        </p>
      )}

      {topic && (
        <>
          <div className="admin-tabs" role="tablist" aria-label="Topic content">
            <button
              type="button"
              role="tab"
              id="tab-upload"
              aria-selected={tab === "upload"}
              aria-controls="panel-upload"
              className={`admin-tabs__tab ${tab === "upload" ? "is-active" : ""}`}
              onClick={() => {
                setTabOverride("upload");
              }}
            >
              Upload
            </button>
            <button
              type="button"
              role="tab"
              id="tab-published"
              aria-selected={tab === "published"}
              aria-controls="panel-published"
              className={`admin-tabs__tab ${tab === "published" ? "is-active" : ""}`}
              onClick={() => {
                setTabOverride("published");
              }}
            >
              Published
            </button>
          </div>

          {tab === "upload" ? (
            <div
              className="admin-unit__body"
              role="tabpanel"
              id="panel-upload"
              aria-labelledby="tab-upload"
            >
              <TopicGenerationUpload
                topicId={topic.id}
                defaultTitle={topic.title}
                publishedHref={`/admin/materials/grades/${gradeKey}/subjects/${subjectId}/topics/${topic.id}?tab=published`}
                onPublished={() => {
                  void reload();
                  openPublished();
                }}
              />
            </div>
          ) : (
            <div
              className="admin-unit__body"
              role="tabpanel"
              id="panel-published"
              aria-labelledby="tab-published"
            >
              {renderPublished()}
            </div>
          )}
        </>
      )}
    </div>
  );
}
