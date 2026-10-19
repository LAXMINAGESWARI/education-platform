import { useEffect, useState } from "react";

import { fetchGuardVerdicts, type GuardDecision, type GuardVerdict } from "../../api/guard";
import { Crumbs } from "../../components/Crumbs";

function decisionLabel(decision: GuardDecision): string {
  switch (decision) {
    case "block":
      return "Blocked";
    case "flag":
      return "Flagged";
    case "abuse":
      return "Refused";
    default: {
      const unexpected: never = decision;
      return unexpected;
    }
  }
}

function surfaceLabel(surface: string): string {
  switch (surface) {
    case "policy_chat":
      return "Policy assistant";
    case "text_to_sql":
      return "Teacher assistant";
    case "generation_review":
      return "Review assistant";
    default:
      return surface;
  }
}

function formatProb(value: number | null): string {
  if (value === null) return "-";
  return value.toFixed(2);
}

function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

export function AdminGuardPage() {
  const [rows, setRows] = useState<GuardVerdict[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchGuardVerdicts()
      .then((items) => {
        if (!cancelled) setRows(items);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load the guard log.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <Crumbs parts={[{ label: "Guard log" }]} />
      <header className="page-head">
        <p className="kicker">Administrator</p>
        <h1>Blocked and flagged messages</h1>
        <p>
          Recent assistant messages the guard blocked, flagged, or refused. Access to student
          data is still enforced by each viewer's role.
        </p>
      </header>

      {loading && <div className="banner banner--info">Loading guard log…</div>}

      {error && (
        <div className="banner banner--warning" role="alert">
          {error}
        </div>
      )}

      {!loading && !error && rows.length === 0 && (
        <div className="banner banner--info" role="status">
          No blocked or flagged messages yet.
        </div>
      )}

      {!loading && !error && rows.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Role</th>
                <th scope="col">Assistant</th>
                <th scope="col">Decision</th>
                <th scope="col">Injection</th>
                <th scope="col">Exfiltration</th>
                <th scope="col">Topic</th>
                <th scope="col">Preview</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{formatWhen(row.created_at)}</td>
                  <td>{row.role}</td>
                  <td>{surfaceLabel(row.surface)}</td>
                  <td>{decisionLabel(row.decision)}</td>
                  <td>{formatProb(row.injection_probability)}</td>
                  <td>{formatProb(row.exfiltration_probability)}</td>
                  <td>{row.topic ?? "-"}</td>
                  <td>{row.message_preview}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
