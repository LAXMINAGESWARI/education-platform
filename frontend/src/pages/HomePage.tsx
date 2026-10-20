import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchLearningDirectory } from "../api/materials";
import type { LearningDirectory, SubjectNode } from "../api/types";
import { ApiError } from "../api/types";
import { StatusBadge } from "../components/BadgePill";
import { Crumbs } from "../components/Crumbs";
import { SchoolMaterialPanel } from "../components/SchoolMaterialPanel";
import { SubjectStatsTable } from "../components/SubjectStatsTable";
import { BACKDROP_CHROME_ANCHOR, setBackdropChrome } from "../lib/backdropChrome";
import { subjectStats, summarizeSubjectStats } from "../lib/subjectStats";
import { subjectProgress } from "../lib/subjectMaterial";

let directoryCache: LearningDirectory | null = null;

export function clearLearningDirectoryCache(): void {
  directoryCache = null;
}

const PLACEHOLDER_SUBJECTS = [
  {
    key: "science",
    name: "Science",
    blurb: "Living world, matter, and energy for Grade 8.",
    locked: true,
  },
  {
    key: "english",
    name: "English",
    blurb: "Reading comprehension and writing craft.",
    locked: true,
  },
  {
    key: "social",
    name: "Social Studies",
    blurb: "Civics, geography, and local history.",
    locked: true,
  },
] as const;

function SubjectMaterialView({
  subjectId,
  subjectName,
  subject,
}: {
  subjectId: string;
  subjectName: string;
  subject: SubjectNode;
}) {
  const topics = subject.topics;
  const { done, total, pct } = subjectProgress(subject);

  useEffect(() => {
    if (topics.length === 0) {
      setBackdropChrome(null);
      return;
    }

    setBackdropChrome({
      progressPercent: pct,
      progressLabel: `Subject completion · ${pct}% · ${done}/${total} topics`,
      statusLabel: done === total && total > 0 ? "Complete" : "In progress",
      complete: done === total && total > 0,
    });

    return () => setBackdropChrome(null);
  }, [done, pct, topics.length, total]);

  const progressSr =
    topics.length > 0
      ? `Subject completion · ${pct}% · ${done}/${total} topics`
      : null;

  return (
    <div className="subject-view">
      <header className="subject-view__head">
        <Crumbs
          parts={[
            { label: "Subjects", to: "/" },
            { label: subjectName },
          ]}
        />
        <div className="subject-view__chrome-slot" {...{ [BACKDROP_CHROME_ANCHOR]: "" }}>
          <h1 id="school-material-heading" className="sr-only">
            School material
          </h1>
          {progressSr && <p className="sr-only">{progressSr}</p>}
        </div>
      </header>

      <section className="material-pane material-pane--school" aria-labelledby="school-material-heading">
        {topics.length === 0 ? (
          <div className="alert alert--info">No school material published yet.</div>
        ) : (
          <SchoolMaterialPanel subjectId={subjectId} subjectName={subjectName} topics={topics} />
        )}
      </section>
    </div>
  );
}

export function HomePage() {
  const { subjectId } = useParams();
  const [directory, setDirectory] = useState<LearningDirectory | null>(directoryCache);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchLearningDirectory();
        if (!cancelled) {
          directoryCache = data;
          setDirectory(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load subjects.");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const subjects = directory?.subjects ?? [];
  const rows = useMemo(() => subjects.map(subjectStats), [subjects]);
  const summary = useMemo(() => summarizeSubjectStats(rows), [rows]);
  const selected = useMemo(() => {
    if (!subjectId) return null;
    return subjects.find((item) => item.id === subjectId) ?? null;
  }, [subjects, subjectId]);

  const knownNames = new Set(subjects.map((s) => s.name.toLowerCase()));
  const placeholders = PLACEHOLDER_SUBJECTS.filter((p) => !knownNames.has(p.name.toLowerCase()));

  if (selected) {
    return (
      <SubjectMaterialView
        subjectId={selected.id}
        subjectName={selected.name}
        subject={selected}
      />
    );
  }

  return (
    <div className="subjects-view">
      <Crumbs parts={[{ label: "Subjects" }]} />
      <header className="page-head">
        <h1>Your subjects</h1>
        <p>
          Pick a subject to open school material, track lesson and quiz progress, and review attempt
          history.
        </p>
      </header>

      {error && (
        <div className="alert alert--warning" role="alert">
          <div className="alert__row">
            <p>{error}</p>
            <button
              type="button"
              className="btn btn--sm btn--outline"
              onClick={() => setAttempt((value) => value + 1)}
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {!directory && !error && (
        <div className="panel panel--static" role="status" aria-live="polite" aria-busy="true">
          <p className="muted">Loading subjects…</p>
        </div>
      )}

      {directory && rows.length > 0 && (
        <section className="stat-kpis" aria-label="Subject overview">
          <article className="stat-kpi">
            <p className="kicker">Enrolled</p>
            <p className="stat-kpi__value">{summary.subjects}</p>
            <p>Subjects on your learning directory</p>
          </article>
          <article className="stat-kpi">
            <p className="kicker">Topics complete</p>
            <p className="stat-kpi__value">
              {summary.topicsDone}
              <span className="stat-kpi__of">/{summary.topicsTotal}</span>
            </p>
            <p>Topics marked complete</p>
          </article>
          <article className="stat-kpi">
            <p className="kicker">Lessons</p>
            <p className="stat-kpi__value">
              {summary.lessonsTotal === 0 ? "—" : summary.lessonsDone}
              {summary.lessonsTotal > 0 && <span className="stat-kpi__of">/{summary.lessonsTotal}</span>}
            </p>
            <p>Published lessons finished</p>
          </article>
          <article className="stat-kpi">
            <p className="kicker">Quizzes passed</p>
            <p className="stat-kpi__value">
              {summary.quizzesAvailable === 0 ? "—" : summary.quizzesPassed}
              {summary.quizzesAvailable > 0 && (
                <span className="stat-kpi__of">/{summary.quizzesAvailable}</span>
              )}
            </p>
            <p>
              {summary.averageProgress === null
                ? "No topic progress yet"
                : `Average topic progress ${summary.averageProgress}%`}
            </p>
          </article>
        </section>
      )}

      {directory && (
        <section className="panel panel--static catalog-panel" aria-labelledby="subject-stats-heading">
          <header className="catalog-panel__head">
            <h2 id="subject-stats-heading">Subject progress</h2>
            <p>Topics finished, lessons, quizzes, and your best score for each enrolled subject.</p>
          </header>
          {rows.length === 0 ? (
            <div className="alert alert--info" role="status">
              No enrolled subjects with published curriculum yet.
            </div>
          ) : (
            <SubjectStatsTable rows={rows} />
          )}
        </section>
      )}

      {directory && placeholders.length > 0 && (
        <section className="catalog-section" aria-labelledby="coming-soon-heading">
          <h2 id="coming-soon-heading" className="section-title">
            Coming soon
          </h2>
          <div className="grid grid--2">
            {placeholders.map((item) => (
              <div key={item.key} className="card is-locked" aria-disabled="true">
                <h2>{item.name}</h2>
                <p>{item.blurb}</p>
                <div className="meta-row">
                  <StatusBadge status="coming_soon" />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
