import { useMemo, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { Crumbs } from "../../components/Crumbs";
import {
  averageMastery,
  findClass,
  useTeacherClasses,
  type TeacherClassStudent,
} from "../../lib/useTeacherClasses";

type SortKey = "name" | "mastery" | "attendance";

/** Nulls last whichever way the column is sorted — "no data" is not a low score. */
function compareNullable(a: number | null, b: number | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return a - b;
}

export function RosterPage() {
  const { sectionId = "" } = useParams();
  const { loading, error, classes } = useTeacherClasses();
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [query, setQuery] = useState("");

  const entry = findClass(classes, sectionId);

  const students = useMemo(() => {
    if (!entry) return [];
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? entry.students.filter(
          (student) =>
            student.fullName.toLowerCase().includes(needle) ||
            student.identifier.toLowerCase().includes(needle),
        )
      : entry.students;

    const sorted = [...filtered];
    if (sortKey === "name") {
      sorted.sort((a, b) => a.fullName.localeCompare(b.fullName));
    } else if (sortKey === "mastery") {
      sorted.sort((a, b) => compareNullable(averageMastery(a), averageMastery(b)));
    } else {
      sorted.sort((a, b) => compareNullable(a.attendancePercent, b.attendancePercent));
    }
    return sorted;
  }, [entry, query, sortKey]);

  if (loading) {
    return <div className="banner banner--info">Loading roster…</div>;
  }
  if (error) {
    return (
      <div className="banner banner--warning" role="alert">
        {error}
      </div>
    );
  }
  if (!entry) {
    return <Navigate to="/teacher" replace />;
  }

  const label = `${entry.gradeName} · ${entry.sectionName}`;

  return (
    <>
      <Crumbs
        parts={[
          { label: "My classes", to: "/teacher" },
          { label, to: `/teacher/classes/${entry.id}` },
          { label: "Students" },
        ]}
      />
      <header className="page-head">
        <p className="kicker">{label} · roster</p>
        <h1>
          {entry.students.length} student{entry.students.length === 1 ? "" : "s"}
        </h1>
        <p>
          Mastery is averaged across {entry.subjects.join(" and ")} — the subject
          {entry.subjects.length === 1 ? "" : "s"} you teach this section.
        </p>
      </header>

      <div className="banner banner--info" role="status">
        Looking for who needs a look? <Link to="/teacher/at-risk">See at-risk flags →</Link>
      </div>

      <div className="roster-controls">
        <div className="field">
          <label className="field__label" htmlFor="roster-search">
            Find a student
          </label>
          <input
            id="roster-search"
            className="field__input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Name or roll number"
          />
        </div>
        <div className="field">
          <span className="field__label">Sort by</span>
          <div className="meta-row" role="group" aria-label="Sort by">
            {(
              [
                ["name", "Name"],
                ["mastery", "Lowest mastery"],
                ["attendance", "Lowest attendance"],
              ] as [SortKey, string][]
            ).map(([key, text]) => (
              <button
                key={key}
                type="button"
                className={`badge ${sortKey === key ? "badge--info" : ""}`}
                aria-pressed={sortKey === key}
                onClick={() => setSortKey(key)}
              >
                {text}
              </button>
            ))}
          </div>
        </div>
      </div>

      {students.length === 0 ? (
        <div className="banner banner--info" role="status">
          No student matches “{query}”.
        </div>
      ) : (
        <div className="teacher-roster" role="table" aria-label={`Roster for ${label}`}>
          <div className="teacher-roster__head" role="row">
            <span role="columnheader">Roll</span>
            <span role="columnheader">Name</span>
            <span role="columnheader">Mastery</span>
            <span role="columnheader">Attendance</span>
          </div>
          {students.map((student) => (
            <RosterRow key={student.id} student={student} sectionId={entry.id} />
          ))}
        </div>
      )}
    </>
  );
}

function RosterRow({ student, sectionId }: { student: TeacherClassStudent; sectionId: string }) {
  const mastery = averageMastery(student);
  const attendance = student.attendancePercent;

  return (
    <div className="teacher-roster__row" role="row">
      <span className="teacher-roster__roll" role="cell" data-label="Roll">
        {student.identifier}
      </span>
      <span className="teacher-roster__name" role="cell" data-label="Name">
        {/* The name is the way in. A roster you cannot click is a dead end, and the
            student page is the screen a teacher opens every day. */}
        <Link to={`/teacher/classes/${sectionId}/students/${student.id}`}>
          {student.fullName}
        </Link>
        {student.bySubject.length > 1 && (
          <span className="roster-subjects">
            {student.bySubject
              .map((s) => `${s.subject} ${s.quizzesTaken > 0 ? `${s.masteryPercent.toFixed(0)}%` : "—"}`)
              .join(" · ")}
          </span>
        )}
      </span>
      <span role="cell" data-label="Mastery">
        {mastery === null ? (
          <span className="badge">No quizzes yet</span>
        ) : (
          <span className="badge">{mastery.toFixed(0)}%</span>
        )}
      </span>
      <span role="cell" data-label="Attendance">
        {attendance === null ? (
          <span className="badge">Not recorded</span>
        ) : (
          <span className="badge">{attendance.toFixed(0)}%</span>
        )}
      </span>
    </div>
  );
}
