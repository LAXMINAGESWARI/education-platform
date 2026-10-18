import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Crumbs } from "../../components/Crumbs";
import { fetchStudentDetail, type StudentDetail } from "../../api/insights";

/**
 * Below this a student is not eligible to sit the end-of-term exam -- an academic
 * eligibility rule, unrelated to the at-risk early-warning engine (which has its own,
 * separately-derived 80% attendance line; see /teacher/at-risk). The two happen to be
 * different numbers on purpose: this one is a school policy, not a statistical cutoff.
 */
const EXAM_ELIGIBILITY_ATTENDANCE = 75;

function formatDay(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export function StudentPage() {
  const { sectionId = "", studentId = "" } = useParams();
  const [student, setStudent] = useState<StudentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchStudentDetail(studentId)
      .then((detail) => {
        if (cancelled) return;
        setStudent(detail);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "That student is not one of yours to open.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [studentId]);

  if (loading) {
    return <div className="banner banner--info">Loading student…</div>;
  }
  if (error || !student) {
    return (
      <>
        <Crumbs
          parts={[
            { label: "My classes", to: "/teacher" },
            { label: "Students", to: `/teacher/classes/${sectionId}/students` },
            { label: "Not found" },
          ]}
        />
        <div className="banner banner--warning" role="alert">
          {error ?? "That student is not one of yours to open."}
        </div>
      </>
    );
  }

  const classLabel = `${student.grade} · ${student.section ?? "Unassigned"}`;
  const attendance = student.attendance_percent;

  return (
    <>
      <Crumbs
        parts={[
          { label: "My classes", to: "/teacher" },
          { label: classLabel, to: `/teacher/classes/${sectionId}` },
          { label: "Students", to: `/teacher/classes/${sectionId}/students` },
          { label: student.full_name },
        ]}
      />
      <header className="page-head">
        <p className="kicker">
          {student.student_identifier} · {classLabel} · {student.academic_period}
        </p>
        <h1>{student.full_name}</h1>
        <p>
          {student.subjects.length === 1
            ? "The subject you teach this student."
            : `The ${student.subjects.length} subjects you teach this student.`}{" "}
          Anything they study with another teacher is not shown here.
        </p>
      </header>

      <div className="student-panels">
        <section className="card" aria-labelledby="attendance-heading">
          <h2 id="attendance-heading">Attendance</h2>
          {attendance === null ? (
            <p className="progress-label">No attendance recorded this term.</p>
          ) : (
            <>
              <div className="student-figure">
                <span
                  className={
                    attendance < EXAM_ELIGIBILITY_ATTENDANCE
                      ? "student-figure__value is-concern"
                      : "student-figure__value"
                  }
                >
                  {attendance.toFixed(0)}%
                </span>
                <span className="progress-label">
                  {student.days_present} of {student.days_counted} days
                </span>
              </div>
              {attendance < EXAM_ELIGIBILITY_ATTENDANCE && (
                <p className="progress-label">
                  Below the {EXAM_ELIGIBILITY_ATTENDANCE}% needed to sit the end-of-term exam.
                </p>
              )}
            </>
          )}

          {student.absences.length > 0 && (
            <>
              <h3 className="student-subhead">Recent days missed</h3>
              <ul className="student-absences">
                {student.absences.map((absence) => (
                  <li key={`${absence.on_date}-${absence.status}`}>
                    <span>{formatDay(absence.on_date)}</span>
                    <span className="badge">{absence.status}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>

        <section aria-labelledby="subjects-heading">
          <div className="topics-section__head">
            <h2 id="subjects-heading">How they are doing</h2>
          </div>
          <div className="student-subject-list">
            {student.subjects.map((standing) => {
              const mastery = Math.round(standing.mastery_percent);
              const attempted = standing.quizzes_taken > 0;
              return (
                <article key={standing.subject} className="card">
                  <div className="meta-row">
                    <h3>{standing.subject}</h3>
                  </div>
                  {attempted ? (
                    <>
                      <div className="progress-label">{mastery}% mastery</div>
                      <div className="progress" aria-hidden="true">
                        <span style={{ width: `${mastery}%` }} />
                      </div>
                      <p className="progress-label">
                        Passed {standing.quizzes_passed} of {standing.quizzes_taken} quizzes ·
                        last attempt {formatDay(standing.last_attempt_at)}
                      </p>
                    </>
                  ) : (
                    <p className="progress-label">No quizzes attempted yet.</p>
                  )}
                  <p className="progress-label">
                    {standing.lessons_completed} of {standing.lessons_started} lessons finished
                  </p>
                </article>
              );
            })}
          </div>
        </section>
      </div>

      <section aria-labelledby="history-heading">
        <div className="topics-section__head">
          <h2 id="history-heading">Quiz history</h2>
          <p>
            {student.attempts.length === 0
              ? "Nothing submitted yet in your subjects."
              : `The last ${student.attempts.length} quizzes they finished in your subjects, newest first.`}
          </p>
        </div>

        {student.attempts.length > 0 && (
          <div className="table-scroll">
            <table className="data-table data-table--cards student-history">
              <thead>
                <tr>
                  <th scope="col">Submitted</th>
                  <th scope="col">Subject</th>
                  <th scope="col">Quiz</th>
                  <th scope="col">Score</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {student.attempts.map((attempt) => (
                  <tr key={attempt.attempt_id}>
                    <td data-label="Submitted">{formatDay(attempt.submitted_at)}</td>
                    <td data-label="Subject">{attempt.subject}</td>
                    <td data-label="Quiz">
                      {attempt.quiz_title}
                      {attempt.attempt_number > 1 && (
                        <span className="badge"> attempt {attempt.attempt_number}</span>
                      )}
                    </td>
                    <td data-label="Score">
                      {attempt.score_percent === null
                        ? "—"
                        : `${Math.round(attempt.score_percent)}%`}
                    </td>
                    <td data-label="Result">
                      {attempt.passed === null ? (
                        <span className="badge">Not marked</span>
                      ) : (
                        <span className={attempt.passed ? "badge badge--ok" : "badge badge--warn"}>
                          {attempt.passed ? "Passed" : "Not passed"}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="meta-row">
        <Link className="badge badge--info" to={`/teacher/classes/${sectionId}/students`}>
          ← Back to the roster
        </Link>
        <Link className="badge" to="/teacher/at-risk">
          See at-risk flags →
        </Link>
      </div>
    </>
  );
}
