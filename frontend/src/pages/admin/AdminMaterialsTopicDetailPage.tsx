import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useParams, useSearchParams } from "react-router-dom";

import { listTopicGenerationRuns } from "../../api/generation";
import { getSubtopicMaterial, getTopicMaterial } from "../../api/materials";
import type { GenerationQaItem, GenerationRun, LessonMaterial, LessonSlide } from "../../api/types";
import { Crumbs, type Crumb } from "../../components/Crumbs";
import { LessonSlideFrame } from "../../components/LessonSlideFrame";
import { MarkdownContent } from "../../components/MarkdownContent";
import { PushButton } from "../../components/PushButton";
import { TopicGenerationUpload } from "../../components/TopicGenerationUpload";
import { TopicItemStats } from "../../components/TopicItemStats";
import { findSummarySlide, slidesForLessonView } from "../../lib/lessonSlides";
import { presentQuizText } from "../../lib/quizText";
import { useAdminDirectory } from "../../lib/useAdminDirectory";

type TabId = "upload" | "published";
type PublishedView = "overview" | "slides";

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

function optionEntries(options: Record<string, string>): [string, string][] {
  return Object.entries(options).sort(([left], [right]) => left.localeCompare(right));
}

function AdminPublishedQuiz({
  items,
  loading,
}: {
  items: GenerationQaItem[];
  loading: boolean;
}) {
  const sorted = useMemo(
    () => [...items].sort((left, right) => left.sequence - right.sequence),
    [items],
  );
  const countLabel =
    sorted.length === 1 ? "1 question" : `${sorted.length} questions`;

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
                  ? `${countLabel} · read-only`
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
        <ol className="admin-published-quiz">
          {sorted.map((item) => (
            <li key={item.question_id} className="admin-published-quiz__item">
              <p className="admin-published-quiz__prompt">
                <strong>Q{item.sequence}.</strong>{" "}
                <MarkdownContent inline>{presentQuizText(item.prompt)}</MarkdownContent>
              </p>
              <ul className="draft-options">
                {optionEntries(item.options).map(([label, text]) => (
                  <li
                    key={label}
                    className={
                      label === item.correct_label ? "draft-option is-correct" : "draft-option"
                    }
                  >
                    <span className="draft-option__label">{label}</span>
                    <span>
                      <MarkdownContent inline>{presentQuizText(text)}</MarkdownContent>
                    </span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function AdminPublishedSlides({
  slides,
  pane,
  onOpenSlides,
}: {
  slides: LessonSlide[];
  pane: "overview" | "slides";
  onOpenSlides: () => void;
}) {
  const [slideIndex, setSlideIndex] = useState(0);
  const summarySlide = findSummarySlide(slides);

  useEffect(() => {
    if (pane === "slides") {
      setSlideIndex(0);
    }
  }, [pane]);

  if (slides.length === 0) {
    return (
      <p className="muted" role="status">
        No published topic lesson.
      </p>
    );
  }

  switch (pane) {
    case "overview":
      return (
        <LessonSlideFrame
          title={summarySlide?.title ?? "Lesson summary"}
          content={summarySlide?.content ?? ""}
          ariaLabel="Topic lesson"
          footer={
            <PushButton size="sm" onClick={onOpenSlides}>
              Continue
            </PushButton>
          }
        />
      );
    case "slides": {
      const slide = slides[slideIndex] ?? slides[0];
      const atEnd = slideIndex >= slides.length - 1;
      return (
        <div className="lesson-slides">
          <LessonSlideFrame
            title={slide.title}
            content={slide.content}
            ariaLabel="Topic lesson slides"
            footer={
              <>
                <div className="slide-nav__controls">
                  <PushButton
                    variant="outline"
                    size="sm"
                    disabled={slideIndex === 0}
                    onClick={() => setSlideIndex((index) => Math.max(0, index - 1))}
                  >
                    Previous
                  </PushButton>
                  <PushButton
                    size="sm"
                    disabled={atEnd}
                    onClick={() =>
                      setSlideIndex((index) => Math.min(slides.length - 1, index + 1))
                    }
                  >
                    Next slide
                  </PushButton>
                </div>
                <p className="slide-nav__status">
                  Slide {slideIndex + 1} of {slides.length}
                </p>
              </>
            }
          />
        </div>
      );
    }
    default: {
      const _never: never = pane;
      void _never;
      return null;
    }
  }
}

function AdminPublishedMaterial({
  topicId,
  subtopicId,
  fallbackTitle,
  hasLesson,
  quizItems,
  quizLoading,
  pane,
  preferSubtopic,
  onOpenSlides,
}: {
  topicId: string;
  subtopicId: string | null;
  fallbackTitle: string;
  hasLesson: boolean;
  quizItems: GenerationQaItem[];
  quizLoading: boolean;
  pane: "overview" | "slides";
  preferSubtopic: boolean;
  onOpenSlides: () => void;
}) {
  const [lesson, setLesson] = useState<LessonMaterial | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const slides = useMemo(
    () => (lesson ? slidesForLessonView({ ...lesson, title: lesson.title || fallbackTitle }) : []),
    [lesson, fallbackTitle],
  );

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
  } else {
    main = (
      <AdminPublishedSlides slides={slides} pane={pane} onOpenSlides={onOpenSlides} />
    );
  }

  return (
    <div className="lesson-view admin-published-material">
      <div className="lesson-layout">
        <section className="lesson-layout__main" aria-label="Published lesson">
          {main}
        </section>
        <aside className="lesson-layout__aside" aria-label="Published quiz">
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
  const [publishedView, setPublishedView] = useState<PublishedView>("overview");
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
    setPublishedView("overview");
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
        onClick:
          tab === "published" && publishedView === "slides"
            ? () => setPublishedView("overview")
            : undefined,
      },
    ];
    if (tab === "published" && publishedView === "slides") {
      parts.push({ label: "Slides" });
    }
    return parts;
  }, [grade, subject, topic, tab, publishedView]);

  if (!loading && !error && grades && !found) {
    return <Navigate to="/admin/materials" replace />;
  }

  function openPublished(view: PublishedView) {
    setTabOverride("published");
    setPublishedView(view);
  }

  function renderPublished() {
    if (!topic) return null;
    switch (publishedView) {
      case "overview":
      case "slides":
        return (
          <AdminPublishedMaterial
            topicId={topic.id}
            subtopicId={lessonSubtopicId}
            fallbackTitle={requestedUnit?.title ?? topic.title}
            hasLesson={hasPublishedLesson}
            quizItems={publishedRun?.qa_items ?? []}
            quizLoading={publishedRunLoading}
            pane={publishedView}
            preferSubtopic={preferSubtopicLesson}
            onOpenSlides={() => setPublishedView("slides")}
          />
        );
      default: {
        const _never: never = publishedView;
        void _never;
        return null;
      }
    }
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
                setPublishedView("overview");
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
                setPublishedView("overview");
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
                onPublished={() => {
                  void reload();
                  openPublished("overview");
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
