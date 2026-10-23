import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import {
  closeReviewRound,
  discardGenerationRun,
  getGenerationReview,
  getGenerationRun,
  isInFlightGenerationPhase,
  listGenerationSteps,
  listTopicGenerationRuns,
  publishGenerationRun,
  rejectGenerationItems,
  retryGenerationRun,
  submitReviewDecision,
  submitTopicGenerationRun,
  subscribeGenerationRun,
} from "../api/generation";
import { ProgressReconnectError } from "../api/progress";
import type { ProgressEnvelope } from "../api/progressEnvelope";
import {
  ApiError,
  type DraftChangeRequest,
  type GenerationRun,
  type GenerationStepId,
  type GenerationStepStatus,
  type GenerationStepTiming,
  type ReviewWorkspace,
  type RunPhase,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { claimHaptic, triggerHaptic } from "../lib/haptics";
import { ROLE_ADMIN } from "../auth/roles";
import { ConfirmDialog } from "./ConfirmDialog";
import { GenerationStepDetailPanel } from "./GenerationStepDetail";
import { ProgressReconnectStatus, ProgressStreamAlert } from "./ProgressReconnect";
import { UploadProgressMeter } from "./UploadProgressMeter";
import { PushButton } from "./PushButton";
import { TopicGenerationQa } from "./TopicGenerationQa";
import { TopicGenerationReview } from "./TopicGenerationReview";

type TopicGenerationUploadProps = {
  topicId: string;
  defaultTitle?: string;
  allowUpload?: boolean;
  publishedHref?: string | null;
  onPublished?: () => void;
};

const GENERATION_STEPS = [
  { id: "indexing", label: "Indexing PDF" },
  { id: "outlining", label: "Determining outline" },
  { id: "outline_review", label: "Outline review" },
  { id: "lesson", label: "Generating lesson" },
  { id: "items", label: "Generating quiz items" },
  { id: "ready", label: "Ready to review" },
] as const;

type StepVisual = GenerationStepStatus;

function generatingActiveStep(run: GenerationRun): GenerationStepId {
  const regenJob = run.jobs.find((job) => job.kind === "regenerate_items");
  if (regenJob !== undefined) return "items";
  const itemsJob = run.jobs.find((job) => job.kind === "items");
  const lessonJob = run.jobs.find((job) => job.kind === "lesson");
  if (lessonJob !== undefined) return "lesson";
  if (itemsJob !== undefined) return "items";
  return "lesson";
}

function failedActiveStep(run: GenerationRun): GenerationStepId {
  if (
    run.jobs.some(
      (job) => job.kind === "lesson" || job.kind === "items" || job.kind === "regenerate_items",
    )
  ) {
    return generatingActiveStep(run);
  }
  if (run.outline) return "outline_review";
  if (run.intake_version_id) return "outlining";
  return "indexing";
}

function activeStepId(run: GenerationRun): GenerationStepId | null {
  switch (run.phase) {
    case "indexing":
      return "indexing";
    case "outlining":
      return "outlining";
    case "outline_review":
      return "outline_review";
    case "generating":
      return generatingActiveStep(run);
    case "qa_review":
      return "ready";
    case "published":
      return null;
    case "failed":
      return failedActiveStep(run);
    case "discarded":
      return null;
    default: {
      const _never: never = run.phase;
      void _never;
      return null;
    }
  }
}

function stepVisual(run: GenerationRun, stepId: GenerationStepId): StepVisual {
  if (run.phase === "discarded") return "pending";
  if (run.phase === "published") return "complete";
  const active = activeStepId(run);
  const order = GENERATION_STEPS.findIndex((step) => step.id === stepId);
  const activeIndex = active ? GENERATION_STEPS.findIndex((step) => step.id === active) : -1;
  if (run.phase === "failed") {
    if (activeIndex >= 0 && order < activeIndex) return "complete";
    if (active === stepId) return "failed";
    return "pending";
  }
  if (activeIndex < 0) return "pending";
  if (order < activeIndex) return "complete";
  if (order === activeIndex) return "current";
  return "pending";
}

function stepVisualLabel(visual: StepVisual): string {
  switch (visual) {
    case "complete":
      return "Complete";
    case "current":
      return "Current";
    case "pending":
      return "Not run yet";
    case "failed":
      return "Failed";
    default: {
      const _never: never = visual;
      return _never;
    }
  }
}

function timingVisual(status: GenerationStepStatus): StepVisual {
  switch (status) {
    case "complete":
    case "current":
    case "pending":
    case "failed":
      return status;
    default: {
      const _never: never = status;
      return _never;
    }
  }
}

function GenerationProgressStepper({
  run,
  timings,
  interactive,
  selectedStep,
  onSelect,
}: {
  run: GenerationRun;
  timings: GenerationStepTiming[];
  interactive: boolean;
  selectedStep: GenerationStepId | null;
  onSelect: (stepId: GenerationStepId) => void;
}) {
  return (
    <ol className="generation-stepper" aria-label="Generation progress">
      {GENERATION_STEPS.map((step) => {
        const timing = timings.find((item) => item.step === step.id);
        const visual = timing ? timingVisual(timing.status) : stepVisual(run, step.id);
        const pending = visual === "pending";
        const hint = timing?.hint ?? (pending ? "This step has not run yet." : null);
        const selected = selectedStep === step.id;
        return (
          <li
            key={step.id}
            className={`generation-stepper__step is-${visual}${selected ? " is-selected" : ""}`}
            aria-current={visual === "current" ? "step" : undefined}
          >
            {interactive ? (
              <button
                type="button"
                id={`generation-step-${step.id}`}
                className="generation-stepper__button"
                disabled={pending}
                aria-pressed={selected}
                title={hint ?? undefined}
                onClick={() => onSelect(step.id)}
              >
                <span className="generation-stepper__label">{step.label}</span>
                <span className="generation-stepper__state">{stepVisualLabel(visual)}</span>
                {hint ? <span className="sr-only">{hint}</span> : null}
              </button>
            ) : (
              <>
                <span className="generation-stepper__label">{step.label}</span>
                <span className="generation-stepper__state">{stepVisualLabel(visual)}</span>
              </>
            )}
          </li>
        );
      })}
    </ol>
  );
}

type ConfirmKind = "accept" | "discard" | "publish" | "rewrite" | "override" | null;

function canStartNewUpload(phase: RunPhase | null): boolean {
  if (phase == null) return true;
  switch (phase) {
    case "indexing":
    case "outlining":
    case "outline_review":
    case "generating":
    case "qa_review":
      return false;
    case "failed":
    case "published":
    case "discarded":
      return true;
    default: {
      const _never: never = phase;
      void _never;
      return false;
    }
  }
}

function inFlightRun(runs: GenerationRun[]): GenerationRun | null {
  return runs.find((item) => isInFlightGenerationPhase(item.phase)) ?? null;
}

function upsertRun(runs: GenerationRun[], next: GenerationRun): GenerationRun[] {
  const index = runs.findIndex((item) => item.id === next.id);
  if (index === -1) return [next, ...runs];
  return runs.map((item, itemIndex) => (itemIndex === index ? next : item));
}

function formatRunWhen(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString();
}

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ProgressReconnectError || err instanceof ApiError) return err.message;
  return fallback;
}

function phaseStatusCopy(run: GenerationRun): string {
  switch (run.phase) {
    case "indexing":
      return "Indexing the uploaded PDF.";
    case "outlining":
      return "Building the outline.";
    case "outline_review":
      return "Outline ready for review.";
    case "generating":
      return generatingStatusCopy(run);
    case "qa_review":
      return "Draft lesson and item bank are ready for QA.";
    case "published":
      return "This run is published.";
    case "failed":
      return run.failure_reason ?? "Generation failed.";
    case "discarded":
      return "This run was discarded.";
    default: {
      const _never: never = run.phase;
      void _never;
      return "";
    }
  }
}

/** Retry refetches a finished or parked review run and does not open another stream. */
function phaseStreamsOnRetry(phase: RunPhase): boolean {
  switch (phase) {
    case "indexing":
    case "outlining":
    case "generating":
      return true;
    case "outline_review":
    case "qa_review":
    case "failed":
    case "published":
    case "discarded":
      return false;
    default: {
      const _never: never = phase;
      void _never;
      return false;
    }
  }
}

function streamLeavesUiIdle(phase: RunPhase): boolean {
  switch (phase) {
    case "outline_review":
    case "qa_review":
    case "generating":
      return true;
    case "indexing":
    case "outlining":
    case "failed":
    case "discarded":
    case "published":
      return false;
    default: {
      const _never: never = phase;
      void _never;
      return false;
    }
  }
}

function generatingStatusCopy(run: GenerationRun): string {
  const regenJob = run.jobs.find((job) => job.kind === "regenerate_items");
  if (regenJob !== undefined) {
    return "Rejected items are being regenerated.";
  }
  const itemsJob = run.jobs.find((job) => job.kind === "items");
  const lessonJob = run.jobs.find((job) => job.kind === "lesson");
  if (lessonJob !== undefined && itemsJob !== undefined) {
    return "Outline accepted. Lesson generation is in progress. Quiz items start after the lesson.";
  }
  if (lessonJob !== undefined) {
    return "Outline accepted. Lesson generation is in progress. Quiz items start after the lesson.";
  }
  if (itemsJob !== undefined) {
    return "Outline accepted. Lesson generation is complete. Quiz item generation is in progress.";
  }
  return "Outline accepted. Queueing lesson generation. Quiz items start after the lesson.";
}

export function TopicGenerationUpload({
  topicId,
  defaultTitle = "",
  allowUpload = true,
  publishedHref = null,
  onPublished,
}: TopicGenerationUploadProps) {
  const { isDevMockSession, hasRole } = useAuth();
  const canClose = hasRole(ROLE_ADMIN);
  const [overrideRationale, setOverrideRationale] = useState("");
  const [title, setTitle] = useState(defaultTitle);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [counts, setCounts] = useState<ProgressEnvelope | null>(null);
  const [runs, setRuns] = useState<GenerationRun[]>([]);
  const [run, setRun] = useState<GenerationRun | null>(null);
  const [workspace, setWorkspace] = useState<ReviewWorkspace | null>(null);
  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const [stepTimings, setStepTimings] = useState<GenerationStepTiming[]>([]);
  const [selectedStep, setSelectedStep] = useState<GenerationStepId | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setTitle(defaultTitle);
  }, [defaultTitle]);

  useEffect(() => {
    if (run?.phase !== "outline_review" && run?.phase !== "qa_review") {
      setWorkspace(null);
    }
  }, [run]);

  function trackRun(next: GenerationRun) {
    setRun(next);
    setRuns((current) => upsertRun(current, next));
  }

  useEffect(() => {
    if (isDevMockSession) return;

    pollAbortRef.current?.abort();
    const abort = new AbortController();
    pollAbortRef.current = abort;

    async function load() {
      setBusy(true);
      setError(null);
      setStreamError(null);
      try {
        const listed = await listTopicGenerationRuns(topicId);
        if (abort.signal.aborted) return;
        setRuns(listed);
        const current = inFlightRun(listed);
        if (!current) {
          setRun(null);
          return;
        }
        setRun(current);
        if (current.phase === "outline_review" || current.phase === "qa_review") {
          const review = await getGenerationReview(current.id);
          if (!abort.signal.aborted) setWorkspace(review);
        }
        if (isInFlightGenerationPhase(current.phase)) {
          if (streamLeavesUiIdle(current.phase) && !abort.signal.aborted) {
            setBusy(false);
          }
          await followRun(current.id, abort);
        }
      } catch (err) {
        if (abort.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
        noteFailure(err, "Could not load the generation run.");
      } finally {
        if (!abort.signal.aborted) setBusy(false);
      }
    }

    void load();
    return () => {
      abort.abort();
    };
  }, [topicId, isDevMockSession]);

  const viewedRunId = run?.id ?? null;
  useEffect(() => {
    setSelectedStep(null);
    if (!canClose || viewedRunId == null) {
      setStepTimings([]);
      return;
    }
    let cancelled = false;
    void listGenerationSteps(viewedRunId)
      .then((rows) => {
        if (!cancelled) setStepTimings(rows);
      })
      .catch(() => {
        if (!cancelled) setStepTimings([]);
      });
    return () => {
      cancelled = true;
    };
  }, [canClose, viewedRunId]);

  useEffect(() => {
    if (selectedStep == null || stepTimings.length === 0) return;
    const timing = stepTimings.find((item) => item.step === selectedStep);
    if (timing != null && !timing.available) setSelectedStep(null);
  }, [selectedStep, stepTimings]);

  useEffect(() => {
    return () => {
      pollAbortRef.current?.abort();
    };
  }, []);

  async function followRun(runId: string, abort: AbortController) {
    if (!abort.signal.aborted) {
      setReconnecting(false);
      setCounts(null);
    }
    try {
      await subscribeGenerationRun(runId, {
        signal: abort.signal,
        onReconnecting: () => {
          if (!abort.signal.aborted) setReconnecting(true);
        },
        onReconnected: () => {
          // First frame, not response headers, so the banner does not flicker.
          if (!abort.signal.aborted) setReconnecting(false);
        },
        onProgress: (progress) => {
          if (!abort.signal.aborted) setCounts(progress);
        },
        onSnapshot: (next) => {
          if (abort.signal.aborted) return;
          trackRun(next);
          if (next.phase === "generating") {
            setBusy(false);
          }
          if (
            (next.phase === "outline_review" || next.phase === "qa_review") &&
            next.jobs.length === 0
          ) {
            setBusy(false);
            void getGenerationReview(next.id).then((review) => {
              if (!abort.signal.aborted) setWorkspace(review);
            });
          }
        },
      });
    } finally {
      if (!abort.signal.aborted) setReconnecting(false);
    }
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!allowUpload || !file || busy || !canStartNewUpload(run?.phase ?? null)) return;

    const resolvedTitle = title.trim() || file.name.replace(/\.pdf$/i, "");
    setBusy(true);
    setError(null);
    setStreamError(null);

    pollAbortRef.current?.abort();
    const abort = new AbortController();
    pollAbortRef.current = abort;

    try {
      const accepted = await submitTopicGenerationRun(topicId, file, resolvedTitle);
      if (abort.signal.aborted) return;
      trackRun({
        id: accepted.run_id,
        topic_id: accepted.topic_id,
        title: resolvedTitle,
        phase: accepted.phase,
        target_item_count: 80,
        submitted_by_user_id: "",
        intake_version_id: null,
        failure_reason: null,
        outline: null,
        draft_lesson_markdown: null,
        jobs: [],
        created_at: new Date().toISOString(),
      });
      await followRun(accepted.run_id, abort);
      if (abort.signal.aborted) return;
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err) {
      if (abort.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
      noteFailure(err, "Upload failed. Try again.");
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }

  async function refreshWorkspace(runId: string) {
    const review = await getGenerationReview(runId);
    setWorkspace(review);
    const next = await getGenerationRun(runId);
    trackRun(next);
    return next;
  }

  async function onApprove() {
    if (!run || !workspace?.open_round || busy) return;
    claimHaptic();
    setBusy(true);
    setError(null);
    try {
      await submitReviewDecision(run.id, workspace.open_round.id, {
        revision_id: workspace.active_revision.id,
        verdict: "approve",
        snapshot_hash: workspace.active_revision.snapshot_hash,
      });
      await refreshWorkspace(run.id);
      triggerHaptic("success");
    } catch (err) {
      triggerHaptic("error");
      setError(errorMessage(err, "Could not submit the approval."));
    } finally {
      setBusy(false);
    }
  }

  async function onRequestChanges(requests: DraftChangeRequest[]) {
    if (!run || !workspace?.open_round || busy || requests.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await submitReviewDecision(run.id, workspace.open_round.id, {
        revision_id: workspace.active_revision.id,
        verdict: "changes_requested",
        snapshot_hash: workspace.active_revision.snapshot_hash,
        requests,
      });
      await refreshWorkspace(run.id);
    } catch (err) {
      setError(errorMessage(err, "Could not submit change requests."));
    } finally {
      setBusy(false);
    }
  }

  async function onAccept() {
    if (!run || !workspace?.open_round || busy) return;
    setConfirm(null);
    setBusy(true);
    setError(null);
    try {
      await closeReviewRound(run.id, workspace.open_round.id, {
        revision_id: workspace.active_revision.id,
        action: "accept_current",
        snapshot_hash: workspace.active_revision.snapshot_hash,
      });
      const next = await getGenerationRun(run.id);
      trackRun(next);
      if (next.phase === "generating") {
        setBusy(false);
      } else if (next.phase === "qa_review") {
        const review = await getGenerationReview(next.id);
        setWorkspace(review);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(errorMessage(err, "Could not accept the outline."));
    } finally {
      setBusy(false);
    }
  }

  async function onRewrite() {
    if (!run || !workspace?.open_round || busy) return;
    setConfirm(null);
    setBusy(true);
    setError(null);
    try {
      await closeReviewRound(run.id, workspace.open_round.id, {
        revision_id: workspace.active_revision.id,
        action: "rewrite",
        snapshot_hash: workspace.active_revision.snapshot_hash,
      });
      await refreshWorkspace(run.id);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(errorMessage(err, "Could not queue the rewrite."));
    } finally {
      setBusy(false);
    }
  }

  async function onOverride() {
    if (!run || !workspace?.open_round || busy || overrideRationale.trim() === "") return;
    setConfirm(null);
    setBusy(true);
    setError(null);
    try {
      if (workspace.active_revision.stage !== "outline") return;
      await closeReviewRound(run.id, workspace.open_round.id, {
        revision_id: workspace.active_revision.id,
        action: "override_and_accept",
        snapshot_hash: workspace.active_revision.snapshot_hash,
        rationale: overrideRationale.trim(),
        replacement: workspace.active_revision.snapshot,
      });
      const next = await getGenerationRun(run.id);
      trackRun(next);
      setOverrideRationale("");
      if (next.phase === "generating") {
        setBusy(false);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(errorMessage(err, "Could not override the outline."));
    } finally {
      setBusy(false);
    }
  }

  async function onDiscard() {
    if (!run || busy) return;
    setConfirm(null);
    setBusy(true);
    setError(null);
    try {
      const discarded = await discardGenerationRun(run.id);
      trackRun(discarded);
    } catch (err) {
      setError(errorMessage(err, "Could not discard the run."));
    } finally {
      setBusy(false);
    }
  }

  async function onRetry() {
    if (!run || busy) return;
    setBusy(true);
    setError(null);
    setStreamError(null);
    pollAbortRef.current?.abort();
    const abort = new AbortController();
    pollAbortRef.current = abort;
    try {
      const next = await retryGenerationRun(run.id);
      if (abort.signal.aborted) return;
      trackRun(next);
      if (isInFlightGenerationPhase(next.phase)) {
        if (streamLeavesUiIdle(next.phase) && !abort.signal.aborted) {
          setBusy(false);
        }
        await followRun(next.id, abort);
      }
    } catch (err) {
      if (abort.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
      noteFailure(err, "Could not retry the run.");
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }

  async function onRejectItems(questionIds: string[]) {
    if (!run || busy || questionIds.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const next = await rejectGenerationItems(run.id, questionIds);
      trackRun(next);
      if (next.phase === "generating") {
        setBusy(false);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(errorMessage(err, "Could not reject the selected items."));
    } finally {
      setBusy(false);
    }
  }

  async function onPublish() {
    if (!run || busy) return;
    claimHaptic();
    setConfirm(null);
    setBusy(true);
    setError(null);
    try {
      const published = await publishGenerationRun(run.id);
      try {
        const next = await getGenerationRun(published.run_id);
        trackRun(next);
      } catch {
        trackRun({
          ...run,
          phase: "published",
          published_lesson_version_id: published.lesson_version_id,
          published_quiz_version_id: published.quiz_version_id,
        });
      }
      onPublished?.();
      triggerHaptic("success");
    } catch (err) {
      triggerHaptic("error");
      setError(errorMessage(err, "Could not publish this run."));
    } finally {
      setBusy(false);
    }
  }

  async function selectHistoryRun(selected: GenerationRun) {
    if (selected.id === run?.id) return;
    pollAbortRef.current?.abort();
    const abort = new AbortController();
    pollAbortRef.current = abort;
    setError(null);
    trackRun(selected);
    requestAnimationFrame(() => {
      const detail = document.getElementById("generation-run-detail");
      if (detail != null && typeof detail.scrollIntoView === "function") {
        detail.scrollIntoView({ block: "nearest" });
      }
    });
    if (selected.phase !== "outline_review" && selected.phase !== "qa_review") {
      setWorkspace(null);
    }
    if (!isInFlightGenerationPhase(selected.phase)) {
      return;
    }
    setBusy(true);
    try {
      if (selected.phase === "outline_review" || selected.phase === "qa_review") {
        const review = await getGenerationReview(selected.id);
        if (!abort.signal.aborted) setWorkspace(review);
      }
      if (isInFlightGenerationPhase(selected.phase)) {
        if (streamLeavesUiIdle(selected.phase) && !abort.signal.aborted) {
          setBusy(false);
        }
        await followRun(selected.id, abort);
      }
    } catch (err) {
      if (abort.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
      noteFailure(err, "Could not load that generation run.");
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }

  function noteFailure(err: unknown, fallback: string) {
    setReconnecting(false);
    if (err instanceof ProgressReconnectError) {
      setError(null);
      setStreamError(err.message);
      return;
    }
    setStreamError(null);
    setError(errorMessage(err, fallback));
  }

  async function resumeProgress() {
    if (!run || busy) return;
    pollAbortRef.current?.abort();
    const abort = new AbortController();
    pollAbortRef.current = abort;
    setBusy(true);
    setError(null);
    setStreamError(null);
    setReconnecting(false);
    try {
      const fresh = await getGenerationRun(run.id);
      if (abort.signal.aborted) return;
      trackRun(fresh);
      if (canClose) {
        try {
          const steps = await listGenerationSteps(fresh.id);
          if (!abort.signal.aborted) setStepTimings(steps);
        } catch {
          if (!abort.signal.aborted) setStepTimings([]);
        }
      }
      if (fresh.phase === "outline_review" || fresh.phase === "qa_review") {
        const review = await getGenerationReview(fresh.id);
        if (!abort.signal.aborted) setWorkspace(review);
      }
      if (phaseStreamsOnRetry(fresh.phase)) {
        await followRun(fresh.id, abort);
      }
    } catch (err) {
      if (abort.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
      noteFailure(err, "Could not resume progress updates.");
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }

  const activeInFlight = inFlightRun(runs);
  const uploadEnabled = Boolean(
    allowUpload && file && !busy && canStartNewUpload(activeInFlight?.phase ?? null),
  );
  const uploadLocked = busy || !canStartNewUpload(activeInFlight?.phase ?? null);

  function renderQa(current: GenerationRun, phase: "qa_review" | "published"): ReactNode {
    return (
      <TopicGenerationQa
        items={current.qa_items ?? []}
        draftLessonMarkdown={current.draft_lesson_markdown}
        publishedLessonVersionId={current.published_lesson_version_id}
        publishedQuizVersionId={current.published_quiz_version_id}
        phase={phase}
        busy={busy}
        canPublish={canClose && phase === "qa_review"}
        canReject={false}
        onRejectSelected={(questionIds) => void onRejectItems(questionIds)}
        onRequestPublish={() => setConfirm("publish")}
        itemValidation={current.item_validation}
      />
    );
  }

  function renderPhase(current: GenerationRun): ReactNode {
    const failed = current.phase === "failed";
    const status = (
      <>
        <ProgressReconnectStatus active={reconnecting} />
        <UploadProgressMeter envelope={counts} />
        <GenerationProgressStepper
          run={current}
          timings={stepTimings}
          interactive={canClose}
          selectedStep={selectedStep}
          onSelect={(stepId) =>
            setSelectedStep((currentStep) => (currentStep === stepId ? null : stepId))
          }
        />
        {canClose && selectedStep != null ? (
          <GenerationStepDetailPanel
            runId={current.id}
            step={selectedStep}
            publishedHref={publishedHref}
            onClose={() => {
              const stepId = selectedStep;
              setSelectedStep(null);
              document.getElementById(`generation-step-${stepId}`)?.focus();
            }}
          />
        ) : null}
        <p className={failed ? "form__error" : "admin-upload__status"} role={failed ? "alert" : "status"}>
          {current.phase}. {phaseStatusCopy(current)}
        </p>
      </>
    );

    switch (current.phase) {
      case "indexing":
      case "outlining":
      case "generating":
        return (
          <>
            {status}
            {canClose ? (
              <div className="admin-upload__actions">
                <PushButton
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => setConfirm("discard")}
                >
                  Cancel run
                </PushButton>
              </div>
            ) : null}
          </>
        );
      case "outline_review":
        return (
          <>
            {status}
            {workspace ? (
              <TopicGenerationReview
                workspace={workspace}
                busy={busy}
                canClose={canClose}
                onApprove={() => void onApprove()}
                onRequestChanges={(requests) => void onRequestChanges(requests)}
                onAcceptCurrent={() => setConfirm("accept")}
                onRewrite={() => setConfirm("rewrite")}
                onOverride={(rationale) => {
                  setOverrideRationale(rationale);
                  setConfirm("override");
                }}
                onDiscard={() => setConfirm("discard")}
              />
            ) : (
              <p className="muted">Loading the review board…</p>
            )}
          </>
        );
      case "failed":
        return (
          <>
            {status}
            {canClose ? (
              <div className="admin-upload__actions">
                {current.intake_version_id ? (
                  <PushButton type="button" disabled={busy} loading={busy} onClick={() => void onRetry()}>
                    Retry
                  </PushButton>
                ) : null}
                <PushButton
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => setConfirm("discard")}
                >
                  Discard run
                </PushButton>
              </div>
            ) : null}
          </>
        );
      case "qa_review":
        return (
          <>
            {status}
            {workspace ? (
              <TopicGenerationReview
                workspace={workspace}
                busy={busy}
                canClose={canClose}
                onApprove={() => void onApprove()}
                onRequestChanges={(requests) => void onRequestChanges(requests)}
                onAcceptCurrent={() => setConfirm("accept")}
                onRewrite={() => setConfirm("rewrite")}
                onOverride={(rationale) => {
                  setOverrideRationale(rationale);
                  setConfirm("override");
                }}
                onDiscard={() => setConfirm("discard")}
              />
            ) : (
              <p className="muted">Loading the review board…</p>
            )}
            {renderQa(current, "qa_review")}
          </>
        );
      case "published":
        return (
          <>
            {status}
            {renderQa(current, "published")}
          </>
        );
      case "discarded":
        return (
          <>
            {status}
            {canClose && current.intake_version_id ? (
              <div className="admin-upload__actions">
                <PushButton type="button" disabled={busy} loading={busy} onClick={() => void onRetry()}>
                  Retry
                </PushButton>
              </div>
            ) : null}
          </>
        );
      default: {
        const _never: never = current.phase;
        void _never;
        return null;
      }
    }
  }

  return (
    <div className="admin-upload">
      {error ? (
        <p className="form__error" role="alert">
          {error}
        </p>
      ) : null}
      {streamError ? (
        <ProgressStreamAlert message={streamError} busy={busy} onRetry={() => void resumeProgress()} />
      ) : null}
      {allowUpload ? (
        <form onSubmit={(event) => void onSubmit(event)}>
          <div className="form__field">
            <label className="form__label" htmlFor="topic-generation-title">
              Title
            </label>
            <input
              id="topic-generation-title"
              className="form__input"
              type="text"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Topic source title"
              disabled={uploadLocked}
            />
          </div>
          <div className="form__field">
            <label className="form__label" htmlFor="topic-generation-file">
              PDF file
            </label>
            <input
              id="topic-generation-file"
              ref={fileInputRef}
              className="form__input"
              type="file"
              accept="application/pdf,.pdf"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              disabled={uploadLocked}
            />
          </div>
          <div className="admin-upload__actions">
            <PushButton type="submit" disabled={!uploadEnabled} loading={busy}>
              {busy ? "Processing…" : "Upload topic PDF"}
            </PushButton>
          </div>
        </form>
      ) : null}
      {run ? (
        <div id="generation-run-detail">{renderPhase(run)}</div>
      ) : busy ? (
        <p className="admin-upload__status" role="status">
          Loading generation run…
        </p>
      ) : allowUpload ? null : runs.length === 0 ? (
        <p className="muted">
          No generation run for this topic yet. An administrator uploads the source PDF.
        </p>
      ) : (
        <p className="muted">Select a previous run to review it.</p>
      )}
      {runs.length > 0 ? (
        <section className="generation-run-history" aria-label="Previous runs">
          <h2 className="generation-run-history__title">Previous runs</h2>
          <ul className="list">
            {runs.map((item) => {
              const selected = item.id === run?.id;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`list-item admin-topic-row ${selected ? "is-selected" : ""}`}
                    aria-current={selected ? "true" : undefined}
                    onClick={() => void selectHistoryRun(item)}
                  >
                    <div>
                      <p className="list-item__title">{item.title}</p>
                      <p className="list-item__meta">
                        {item.phase} · {formatRunWhen(item.created_at)}
                      </p>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      <ConfirmDialog
        open={confirm === "accept"}
        title={workspace?.active_revision.stage === "qa" ? "Accept this content?" : "Accept this outline?"}
        body={
          workspace?.active_revision.stage === "qa"
            ? "This seals the teacher round. Publishing remains a separate step so students do not see drafts."
            : "This freezes subtopics and quotas and queues item and lesson generation."
        }
        onDismiss={() => setConfirm(null)}
        actions={[
          { label: "Cancel", variant: "outline" },
          {
            label:
              workspace?.active_revision.stage === "qa"
                ? "Accept current content"
                : "Accept current outline",
            onClick: () => void onAccept(),
          },
        ]}
      />
      <ConfirmDialog
        open={confirm === "rewrite"}
        title={workspace?.active_revision.stage === "qa" ? "Rewrite this content?" : "Rewrite this outline?"}
        body={
          workspace?.active_revision.stage === "qa"
            ? "The worker will repair only the requested lesson sections and quiz items, freeze a new content revision, and open round 2."
            : "The worker will collate teacher requests into one new frozen revision and open round 2."
        }
        onDismiss={() => setConfirm(null)}
        actions={[
          { label: "Cancel", variant: "outline" },
          { label: "Rewrite from requests", onClick: () => void onRewrite() },
        ]}
      />
      <ConfirmDialog
        open={confirm === "override"}
        title="Override and accept this outline?"
        body="This records a new frozen override of the current outline, then freezes subtopics and quotas and queues item and lesson generation."
        onDismiss={() => setConfirm(null)}
        actions={[
          { label: "Cancel", variant: "outline" },
          { label: "Override and accept", onClick: () => void onOverride() },
        ]}
      />
      <ConfirmDialog
        open={confirm === "discard"}
        title="Discard this run?"
        body={
          workspace?.active_revision.stage === "qa"
            ? "Unpublished drafts will be discarded. Live student material is unchanged. You can retry this PDF or upload another."
            : "This run will be cancelled. You can retry the same PDF or upload another."
        }
        onDismiss={() => setConfirm(null)}
        actions={[
          { label: "Cancel", variant: "outline" },
          { label: "Discard run", onClick: () => void onDiscard() },
        ]}
      />
      <ConfirmDialog
        open={confirm === "publish"}
        title="Publish this run?"
        body="This copies the draft lesson to students and releases the topic quiz. Publishing cannot be undone from this screen."
        onDismiss={() => setConfirm(null)}
        actions={[
          { label: "Cancel", variant: "outline" },
          { label: "Publish to students", onClick: () => void onPublish() },
        ]}
      />
    </div>
  );
}
