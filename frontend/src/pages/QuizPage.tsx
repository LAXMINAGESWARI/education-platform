import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { buildSubmitPayload, startAttempt, submitAttempt } from "../api/attempts";
import { fetchLearningDirectory } from "../api/materials";
import type { StartAttemptResponse } from "../api/types";
import { ApiError } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Crumbs } from "../components/Crumbs";
import { MarkdownContent } from "../components/MarkdownContent";
import { PushButton } from "../components/PushButton";
import { installQuizCopyGuard, QUIZ_COPY_MESSAGE } from "../lib/copyGuard";
import { claimHaptic, triggerHaptic } from "../lib/haptics";
import { resolvePathFromAttempt, resolvePathFromQuizId, learningPathCrumb, type LearningPath } from "../lib/learningPath";
import { presentQuizText } from "../lib/quizText";

export function QuizPage() {
  const { quizId = "" } = useParams();
  const navigate = useNavigate();
  const [attempt, setAttempt] = useState<StartAttemptResponse | null>(null);
  const [path, setPath] = useState<LearningPath | null>(null);
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [starting, setStarting] = useState(true);
  const [lockDialog, setLockDialog] = useState<{ title: string; body: string } | null>(null);
  const submittingRef = useRef(false);
  const copyGuardCleanup = useRef<(() => void) | null>(null);
  const copyNoticeTimer = useRef<number | null>(null);
  const [copyBlocked, setCopyBlocked] = useState(false);

  const showCopyNotice = useCallback(() => {
    triggerHaptic("error");
    setCopyBlocked(true);
    if (copyNoticeTimer.current != null) window.clearTimeout(copyNoticeTimer.current);
    copyNoticeTimer.current = window.setTimeout(() => {
      setCopyBlocked(false);
      copyNoticeTimer.current = null;
    }, 2200);
  }, []);

  const question = attempt?.questions[index];
  const answeredCount = useMemo(() => Object.keys(answers).length, [answers]);
  const allAnswered = attempt ? answeredCount === attempt.questions.length : false;
  const passMark = attempt ? Math.round(attempt.pass_threshold_percent) : 70;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setStarting(true);
      setError(null);
      setAttempt(null);
      setAnswers({});
      setIndex(0);
      try {
        const [data, directory] = await Promise.all([
          startAttempt(quizId),
          fetchLearningDirectory().catch(() => null),
        ]);
        if (cancelled) return;
        setAttempt(data);
        setPath(directory ? resolvePathFromAttempt(directory, data) : null);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof ApiError ? err.message : "Could not start quiz.";
        setError(message);
        try {
          const directory = await fetchLearningDirectory();
          if (!cancelled) setPath(resolvePathFromQuizId(directory, quizId));
        } catch {
          /* ignore */
        }
        if (err instanceof ApiError && err.status === 403) {
          setLockDialog({
            title: "Quiz locked",
            body: message || "Finish the lesson before starting this quiz.",
          });
        }
      } finally {
        if (!cancelled) setStarting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [quizId]);

  const showCopyNoticeRef = useRef(showCopyNotice);
  showCopyNoticeRef.current = showCopyNotice;

  // Attach during commit. useEffect runs after paint, and findBy can observe the
  // question text before that effect, so a copy event sometimes missed the guard.
  const setQuizLockNode = useCallback((node: HTMLElement | null) => {
    copyGuardCleanup.current?.();
    copyGuardCleanup.current = null;
    if (node === null) return;
    copyGuardCleanup.current = installQuizCopyGuard(node, () => {
      showCopyNoticeRef.current();
    });
  }, []);

  useEffect(() => {
    return () => {
      copyGuardCleanup.current?.();
      copyGuardCleanup.current = null;
      if (copyNoticeTimer.current != null) window.clearTimeout(copyNoticeTimer.current);
    };
  }, []);

  function selectOption(label: string) {
    if (!question) return;
    setAnswers((prev) => ({ ...prev, [question.number]: label }));
  }

  async function onSubmit() {
    if (!attempt || !allAnswered || submittingRef.current) return;
    claimHaptic();
    submittingRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await submitAttempt(attempt.id, buildSubmitPayload(answers));
      triggerHaptic("success");
      navigate(`/attempts/${result.id}`, {
        replace: true,
        state: { result, path },
      });
    } catch (err) {
      triggerHaptic("error");
      submittingRef.current = false;
      setError(err instanceof ApiError ? err.message : "Could not submit quiz.");
      setBusy(false);
    }
  }

  const lessonPath = path?.lessonPath;
  const lessonCrumb = path ? learningPathCrumb(path) : null;
  const backToLesson = lessonPath ?? path?.subjectPath ?? null;
  const backToLessonLabel = lessonPath ? "Open lesson" : "Back to subject";

  return (
    <>
      {starting && (
        <div className="center-state" role="status">
          Starting quiz…
        </div>
      )}

      {!starting && error && !attempt && (
        <div className="center-state">
          <p className="form__error" role="alert">
            {error}
          </p>
          {backToLesson && (
            <div className="actions" style={{ justifyContent: "center" }}>
              <Link to={backToLesson} className="btn btn--soft btn--sm">
                {backToLessonLabel}
              </Link>
            </div>
          )}
        </div>
      )}

      {!starting && attempt && question && (
        <div className="stack-gap">
          {path && (
            <Crumbs
              parts={[
                { label: "Subjects", to: "/" },
                { label: path.subjectName, to: path.subjectPath },
                ...(lessonCrumb ? [lessonCrumb] : []),
                { label: "Quiz" },
              ]}
            />
          )}

          <header className="page-head">
            <p className="kicker">
              Quiz · Attempt {attempt.attempt_number}
              {attempt.scope === "topic_mastery" ? " · overall" : " · subtopic"}
            </p>
            <h1>{attempt.title}</h1>
          </header>

          <p className="quiz-progress">
            Question {index + 1} of {attempt.questions.length} · {answeredCount} answered · pass
            mark {passMark}%
          </p>

          <article key={question.number} ref={setQuizLockNode} className="panel quiz-lock">
            {question.difficulty && <span className="difficulty">{question.difficulty}</span>}
            <h2 className="question-prompt">
              <MarkdownContent inline>{presentQuizText(question.prompt)}</MarkdownContent>
            </h2>
            <div
              className="options"
              role="radiogroup"
              aria-required="true"
              aria-label={`Question ${question.number}`}
            >
              {question.options.map((opt) => {
                const selected = answers[question.number] === opt.label;
                return (
                  <button
                    key={opt.label}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    className={`option ${selected ? "is-selected" : ""}`}
                    onClick={() => selectOption(opt.label)}
                    disabled={busy}
                  >
                    <span className="option__mark">{opt.label}</span>
                    <span className="option__text">
                      <MarkdownContent inline>{presentQuizText(opt.text)}</MarkdownContent>
                    </span>
                  </button>
                );
              })}
            </div>
          </article>

          {copyBlocked && (
            <p className="quiz-copy-toast" role="status">
              {QUIZ_COPY_MESSAGE}
            </p>
          )}

          {error && (
            <p className="form__error" role="alert">
              {error}
            </p>
          )}

          <div className="actions quiz-actions" style={{ marginTop: 0 }}>
            <PushButton
              variant="outline"
              size="sm"
              disabled={index === 0 || busy}
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
            >
              Previous
            </PushButton>
            {index < attempt.questions.length - 1 ? (
              <PushButton
                size="sm"
                disabled={!answers[question.number] || busy}
                onClick={() => setIndex((i) => i + 1)}
              >
                Next question
              </PushButton>
            ) : (
              <PushButton
                size="sm"
                loading={busy}
                disabled={!allAnswered}
                onClick={() => void onSubmit()}
              >
                Submit quiz
              </PushButton>
            )}
          </div>

          {!allAnswered && index === attempt.questions.length - 1 && (
            <p className="alert alert--warning" role="status">
              Answer every question before you submit.
            </p>
          )}
        </div>
      )}

      <ConfirmDialog
        open={lockDialog != null}
        title={lockDialog?.title ?? ""}
        body={lockDialog?.body ?? ""}
        onDismiss={() => setLockDialog(null)}
        actions={[
          { label: "Got it", variant: "soft" },
          ...(backToLesson
            ? [
                {
                  label: backToLessonLabel,
                  onClick: () => navigate(backToLesson),
                },
              ]
            : []),
        ]}
      />
    </>
  );
}
