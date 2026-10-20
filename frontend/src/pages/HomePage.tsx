import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchLearningDirectory } from "../api/materials";
import type { LearningDirectory, SubjectNode } from "../api/types";
import { ApiError } from "../api/types";
import { Crumbs } from "../components/Crumbs";
import { SubjectDetail, SubjectRouteStatus } from "../components/SubjectDetail";
import { subjectBlurb, subjectProgress } from "../lib/subjectMaterial";

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

export function HomePage() {
  const { subjectId } = useParams();
  const [directory, setDirectory] = useState<LearningDirectory | null>(directoryCache);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await fetchLearningDirectory();
        if (!cancelled) {
          directoryCache = data;
          setDirectory(data);
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
  }, []);

  const subjects = directory?.subjects ?? [];
  const selected =
    subjectId && directory
      ? (directory.subjects.find((item) => item.id === subjectId) ?? null)
      : null;

  const knownNames = new Set(subjects.map((s) => s.name.toLowerCase()));
  const placeholders = PLACEHOLDER_SUBJECTS.filter((p) => !knownNames.has(p.name.toLowerCase()));

  if (subjectId) {
    if (!directory && !error) {
      return <SubjectRouteStatus kind="loading" />;
    }
    if (!directory && error) {
      return <SubjectRouteStatus kind="error" message={error} />;
    }
    if (!selected) {
      return <SubjectRouteStatus kind="missing" />;
    }
    return <SubjectDetail subject={selected} />;
  }

  return (
    <>
      <Crumbs parts={[{ label: "Subjects" }]} />
      <header className="page-head">
        <h1 className="sr-only">Your subjects</h1>
        <p>
          Pick a subject to open school material, track lesson and quiz progress, and review attempt
          history.
        </p>
      </header>

      {error && (
        <p className="form__error" role="alert">
          {error}
        </p>
      )}

      {!directory && !error && (
        <p className="muted" role="status">
          Loading subjects…
        </p>
      )}

      {directory && subjects.length === 0 && placeholders.length === 0 && (
        <div className="alert alert--info">
          No enrolled subjects with published curriculum yet.
        </div>
      )}

      {(subjects.length > 0 || placeholders.length > 0) && (
        <div className="grid grid--2">
          {subjects.map((subject) => (
            <SubjectCard key={subject.id} subject={subject} />
          ))}
          {placeholders.map((item) => (
            <div key={item.key} className="card is-locked" aria-disabled="true">
              <h2>{item.name}</h2>
              <p>{item.blurb}</p>
              <div className="progress-label">Coming soon</div>
              <div className="meta-row">
                <span className="badge badge--locked">Locked</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function SubjectCard({ subject }: { subject: SubjectNode }) {
  const prog = subjectProgress(subject);
  if (subject.topics.length === 0) {
    return (
      <div className="card is-locked">
        <h2>{subject.name}</h2>
        <p>{subjectBlurb(subject)}</p>
        <div className="progress-label">Coming soon</div>
        <div className="meta-row">
          <span className="badge badge--locked">Locked</span>
        </div>
      </div>
    );
  }
  return (
    <Link to={`/subjects/${subject.id}`} className="card">
      <h2>{subject.name}</h2>
      <p>{subjectBlurb(subject)}</p>
      <div className="progress-label">
        {prog.done}/{prog.total} topics complete · {prog.pct}%
      </div>
      <div
        className={`progress ${prog.pct >= 100 ? "progress--complete" : "progress--in-progress"}`}
        aria-hidden="true"
      >
        <span style={{ width: `${prog.pct}%` }} />
      </div>
      <div className="meta-row">
        <span className="badge badge--info">School material</span>
      </div>
    </Link>
  );
}
