import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listQuizAttempts } from "../api/attempts";
import type { AttemptHistoryItem } from "../api/types";
import { AttemptHistoryList } from "./AttemptHistory";
import { QuizPerformanceChart } from "./QuizPerformanceChart";

export function PastAttempts({
  quizId,
  historyPath,
}: {
  quizId: string | null;
  historyPath: string;
}) {
  const [attempts, setAttempts] = useState<AttemptHistoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!quizId) {
      setAttempts([]);
      setError(null);
      return;
    }
    let cancelled = false;
    setAttempts(null);
    setError(null);
    void listQuizAttempts(quizId)
      .then((rows) => {
        if (!cancelled) setAttempts(rows);
      })
      .catch(() => {
        if (!cancelled) {
          setAttempts([]);
          setError("Could not load past attempts.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [quizId]);

  return (
    <section className="feedback-history" aria-label="Past attempts">
      <h2 className="section-title">Past attempts</h2>
      {attempts === null ? (
        <p className="progress-label" role="status">
          Loading attempts…
        </p>
      ) : (
        <>
          {error && (
            <p className="form__error" role="alert">
              {error}
            </p>
          )}
          <QuizPerformanceChart attempts={attempts} />
          <AttemptHistoryList attempts={attempts} />
        </>
      )}
      <Link to={historyPath} className="btn btn--soft btn--sm">
        Show full history
      </Link>
    </section>
  );
}
