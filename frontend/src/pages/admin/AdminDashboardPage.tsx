import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import type { AtRiskFlag } from "../../api/atRisk";
import { fetchDashboardSummary, type DashboardSummary } from "../../api/insights";
import { Crumbs } from "../../components/Crumbs";
import {
  countTooFewToCompare,
  coverageMatrix,
  curriculumCoverage,
  formatPercent,
  hasEnoughEvidence,
  isLowParticipation,
  MIN_SCORED_STUDENTS,
  passRate,
  plural,
  STRONG_MASTERY_PERCENT,
  summariseAtRisk,
  weakestSubjects,
} from "../../lib/adminDashboard";
import type { AdminGrade } from "../../lib/adminCurriculumLive";
import { useAdminDirectory } from "../../lib/useAdminDirectory";
import { useAtRiskFlags } from "../../lib/useAtRiskFlags";

import "./AdminDashboardPage.css";

type SummaryState = {
  loading: boolean;
  error: string | null;
  summary: DashboardSummary | null;
};

function useDashboardSummary(): SummaryState {
  const [state, setState] = useState<SummaryState>({ loading: true, error: null, summary: null });

  useEffect(() => {
    let cancelled = false;
    fetchDashboardSummary()
      .then((summary) => {
        if (!cancelled) setState({ loading: false, error: null, summary });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            loading: false,
            error: err instanceof Error ? err.message : "Could not load school numbers.",
            summary: null,
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

/** Each section loads on its own, so one failing request never blanks the whole page. */
export function AdminDashboardPage() {
  const { loading: summaryLoading, error: summaryError, summary } = useDashboardSummary();
  const { loading: flagsLoading, error: flagsError, flags } = useAtRiskFlags();
  const { loading: directoryLoading, error: directoryError, grades } = useAdminDirectory();

  return (
    <div className="admin-dashboard">
      <Crumbs parts={[{ label: "Dashboard" }]} />
      <header className="page-head">
        <p className="kicker">Administrator · overview</p>
        <h1>Dashboard</h1>
        <p>What needs your attention, how the school is doing, and where the curriculum has gaps.</p>
      </header>

      <AttentionSection loading={flagsLoading} error={flagsError} flags={flags} />

      <section className="admin-dashboard__section" aria-labelledby="dash-glance">
        <h2 id="dash-glance" className="admin-dashboard__title">
          School at a glance
        </h2>
        {summaryLoading && <div className="banner banner--info">Loading school numbers…</div>}
        {summaryError && (
          <div className="banner banner--warning" role="alert">
            {summaryError}
          </div>
        )}
        {summary && (
          <div className="admin-dashboard__stats">
            <Stat
              label="Students"
              value={String(summary.total_students)}
              note="Enrolled in at least one subject"
            />
            <Stat
              label="Average attendance"
              value={formatPercent(summary.average_attendance)}
              note="Whole-day attendance per student"
            />
            <Stat
              label="Average mastery"
              value={formatPercent(summary.average_mastery)}
              note="Across subjects with quiz attempts"
            />
          </div>
        )}
      </section>

      <section className="admin-dashboard__section" aria-labelledby="dash-look-into">
        <h2 id="dash-look-into" className="admin-dashboard__title">
          Subjects to look into
        </h2>
        {summaryLoading && <div className="banner banner--info">Loading subject results…</div>}
        {summary && <SubjectsToLookInto summary={summary} />}
      </section>

      <section className="admin-dashboard__section" aria-labelledby="dash-coverage">
        <h2 id="dash-coverage" className="admin-dashboard__title">
          Curriculum coverage
        </h2>
        {directoryLoading && <div className="banner banner--info">Loading curriculum…</div>}
        {directoryError && (
          <div className="banner banner--warning" role="alert">
            {directoryError}
          </div>
        )}
        {grades && <Coverage grades={grades} />}
      </section>
    </div>
  );
}

/** A headline number. Every card has a note, so the three values line up in a row. */
function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="admin-dashboard__stat">
      <p className="admin-dashboard__stat-label">{label}</p>
      <p className="admin-dashboard__stat-value">{value}</p>
      <p className="admin-dashboard__stat-note">{note}</p>
    </div>
  );
}

/** Good news gets its own look, so "nothing to do" reads differently from a warning. */
function AllGood({ children }: { children: ReactNode }) {
  return (
    <div className="admin-dashboard__ok" role="status">
      <span className="admin-dashboard__ok-icon" aria-hidden="true">
        ✓
      </span>
      <div>{children}</div>
    </div>
  );
}

function AttentionSection({
  loading,
  error,
  flags,
}: {
  loading: boolean;
  error: string | null;
  flags: AtRiskFlag[];
}) {
  const counts = summariseAtRisk(flags);

  return (
    <section className="admin-dashboard__section" aria-labelledby="dash-attention">
      <h2 id="dash-attention" className="admin-dashboard__title">
        Needs your attention
      </h2>
      {loading && <div className="banner banner--info">Checking at-risk flags…</div>}
      {error && (
        <div className="banner banner--warning" role="alert">
          {error}
        </div>
      )}
      {!loading && !error && counts.flags === 0 && (
        <AllGood>
          <p>No students are flagged as at risk right now.</p>
          <Link to="/admin/at-risk" className="admin-dashboard__link">
            Open at-risk flags →
          </Link>
        </AllGood>
      )}
      {!loading && !error && counts.flags > 0 && (
        <div className="panel admin-dashboard__attention">
          <p className="admin-dashboard__lead">
            <strong>{plural(counts.students, "student")}</strong> flagged as at risk (
            {plural(counts.flags, "active flag")})
          </p>
          <div className="meta-row">
            {counts.urgent > 0 && <span className="badge badge--warn">{counts.urgent} urgent</span>}
            {counts.attention > 0 && (
              <span className="badge badge--info">{counts.attention} attention</span>
            )}
            {counts.monitor > 0 && <span className="badge">{counts.monitor} monitor</span>}
          </div>
          <Link to="/admin/at-risk" className="admin-dashboard__link">
            Review at-risk flags →
          </Link>
        </div>
      )}
    </section>
  );
}

function SubjectsToLookInto({ summary }: { summary: DashboardSummary }) {
  const weakest = weakestSubjects(summary.subjects);
  const tooFew = countTooFewToCompare(summary.subjects);
  const tooFewNote =
    tooFew > 0
      ? `${plural(tooFew, "subject")} not shown yet: fewer than ${MIN_SCORED_STUDENTS} students have taken a quiz.`
      : null;

  if (weakest.length === 0) {
    const anyComparable = summary.subjects.some(hasEnoughEvidence);
    return (
      <>
        {anyComparable ? (
          <AllGood>
            <p>
              Every subject with enough quiz results is averaging {STRONG_MASTERY_PERCENT}% or
              above.
            </p>
          </AllGood>
        ) : (
          <div className="banner banner--info" role="status">
            No subject has quiz results from at least {MIN_SCORED_STUDENTS} students yet, so there
            is nothing to compare.
          </div>
        )}
        {tooFewNote && <p className="progress-label admin-dashboard__footnote">{tooFewNote}</p>}
      </>
    );
  }

  return (
    <div className="panel">
      <p className="progress-label">
        Subjects averaging below {STRONG_MASTERY_PERCENT}% quiz mastery, lowest first. This shows
        where students are struggling, not why: the topic's difficulty, the quiz questions,
        attendance, and how many students took part all play a role.
      </p>
      <ol className="admin-dashboard__weakest">
        {weakest.map((subject) => {
          const mastery = subject.average_mastery ?? 0;
          const rate = passRate(subject);
          const tone =
            mastery < 50 ? "progress--danger" : mastery < 70 ? "progress--warn" : "progress--ok";
          return (
            <li key={`${subject.grade}-${subject.subject}`}>
              <div className="subject-bar-row__head">
                <span>
                  {subject.grade} · {subject.subject}
                </span>
                <span>{formatPercent(subject.average_mastery)}</span>
              </div>
              <div className={`progress ${tone}`} aria-hidden="true">
                <span style={{ width: `${Math.min(100, Math.max(0, mastery))}%` }} />
              </div>
              <div className="meta-row">
                <span className="progress-label">
                  {subject.students_attempted} of {plural(subject.students, "student")} attempted a
                  quiz
                  {rate !== null && ` · ${Math.round(rate)}% of attempts passed`}
                </span>
                {isLowParticipation(subject) && (
                  <span
                    className="badge badge--info"
                    title="Fewer than 80% of students have taken a quiz, so this average may change."
                  >
                    Low participation
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {tooFewNote && <p className="progress-label admin-dashboard__footnote">{tooFewNote}</p>}
    </div>
  );
}

function Coverage({ grades }: { grades: AdminGrade[] }) {
  const coverage = curriculumCoverage(grades);
  const matrix = coverageMatrix(coverage);

  if (coverage.grades.length === 0) {
    return (
      <div className="banner banner--info" role="status">
        No grades in the curriculum yet.
      </div>
    );
  }

  const complete = coverage.subjectsWithContent === coverage.totalSubjects;
  const totals = `${plural(coverage.totalTopics, "topic")} published in total.`;
  const everySubject = `Every subject has published topics: ${plural(coverage.totalSubjects, "subject")}, ${plural(coverage.totalTopics, "topic")} in total.`;

  return (
    <>
      {complete ? (
        <AllGood>
          <p>{everySubject}</p>
        </AllGood>
      ) : (
        <div className="banner banner--warning" role="status">
          {coverage.totalSubjects - coverage.subjectsWithContent} of{" "}
          {plural(coverage.totalSubjects, "subject")} have no published topics yet. {totals}
        </div>
      )}
      <div className="panel admin-dashboard__coverage">
        <p className="progress-label">
          Topics and quizzes per grade and subject. Yellow cells have no content yet. Select a cell
          to open that subject's materials.
        </p>
        <div className="heatmap-scroll">
          <table className="heatmap admin-coverage">
            <thead>
              <tr>
                <th scope="col">Grade</th>
                {matrix.subjects.map((name) => (
                  <th scope="col" key={name}>
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row">
                    <span className="heatmap__class">{row.name}</span>
                    {row.emptySubjects > 0 && (
                      <span className="heatmap__count admin-coverage__gap">
                        {row.emptySubjects} without content
                      </span>
                    )}
                  </th>
                  {row.cells.map((cell, index) => {
                    const subjectName = matrix.subjects[index];
                    if (cell === null) {
                      return (
                        <td
                          key={subjectName}
                          className="admin-coverage__none"
                          title="Not offered in this grade"
                        >
                          —
                        </td>
                      );
                    }
                    const to = `/admin/materials/grades/${row.key}/subjects/${cell.id}`;
                    if (cell.topics === 0) {
                      return (
                        <td key={subjectName} className="admin-coverage__empty">
                          <Link to={to} aria-label={`${row.name} ${subjectName}: no content yet`}>
                            None yet
                          </Link>
                        </td>
                      );
                    }
                    const topics = plural(cell.topics, "topic");
                    const quizzes = plural(cell.quizzes, "quiz", "quizzes");
                    return (
                      <td key={subjectName}>
                        <Link to={to} aria-label={`${row.name} ${subjectName}: ${topics}, ${quizzes}`}>
                          <span className="admin-coverage__topics">{topics}</span>
                          <span className="heatmap__count">{quizzes}</span>
                        </Link>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
