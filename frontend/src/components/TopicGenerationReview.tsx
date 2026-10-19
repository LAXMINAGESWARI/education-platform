import { useMemo, useState } from "react";

import type {
  ChangeKind,
  ContentRevision,
  DraftChangeRequest,
  LessonField,
  OutlineField,
  OutlineNodeSnapshot,
  OutlineRevision,
  QuizField,
  ReviewWorkspace,
} from "../api/types";
import { presentQuizText } from "../lib/quizText";
import { GenerationReviewAssistant } from "./GenerationReviewAssistant";
import { MarkdownContent } from "./MarkdownContent";
import { PushButton } from "./PushButton";

const CHANGE_KINDS: ChangeKind[] = [
  "curriculum_alignment",
  "factual_accuracy",
  "pedagogy",
  "structure",
  "assessment_validity",
  "accessibility",
  "other",
];

const OUTLINE_FIELDS: OutlineField[] = [
  "whole_node",
  "title",
  "proposed_outcomes",
  "weight",
  "outline_structure",
];

const LESSON_FIELDS: LessonField[] = ["whole_section", "heading", "body"];
const QUIZ_FIELDS: QuizField[] = ["whole_item", "prompt", "options", "answer_key", "rationales"];
const QA_CHANGE_KINDS: ChangeKind[] = [
  "factual_accuracy",
  "pedagogy",
  "assessment_validity",
  "answer_key",
  "accessibility",
  "other",
];

type TopicGenerationReviewProps = {
  workspace: ReviewWorkspace;
  busy: boolean;
  canClose: boolean;
  onApprove: () => void;
  onRequestChanges: (requests: DraftChangeRequest[]) => void;
  onAcceptCurrent: () => void;
  onRewrite: () => void;
  onOverride: (rationale: string) => void;
  onDiscard: () => void;
};

function requestsForNode(workspace: ReviewWorkspace, nodeKey: string) {
  return workspace.request_threads.filter(
    (thread) => thread.change_request.node_key === nodeKey,
  );
}

function requestsForSection(workspace: ReviewWorkspace, sectionKey: string) {
  return workspace.request_threads.filter(
    (thread) => thread.change_request.section_key === sectionKey,
  );
}

function requestsForItem(workspace: ReviewWorkspace, itemKey: string) {
  return workspace.request_threads.filter(
    (thread) => thread.change_request.item_key === itemKey,
  );
}

function reviewerStateLabel(state: ReviewWorkspace["reviewer_states"][number]["state"]): string {
  switch (state) {
    case "pending":
      return "Pending";
    case "approved":
      return "Approved";
    case "changes_requested":
      return "Requested changes";
    case "abstained":
      return "Abstained";
    default: {
      const _never: never = state;
      return _never;
    }
  }
}

function fieldLabel(field: OutlineField | LessonField | QuizField): string {
  switch (field) {
    case "whole_node":
      return "Whole node";
    case "title":
      return "Title";
    case "parent":
      return "Parent";
    case "weight":
      return "Weight";
    case "proposed_outcomes":
      return "Proposed outcomes";
    case "subtopic_match":
      return "Subtopic match";
    case "outline_structure":
      return "Outline structure";
    case "whole_section":
      return "Whole section";
    case "heading":
      return "Heading";
    case "body":
      return "Body";
    case "whole_item":
      return "Whole item";
    case "prompt":
      return "Prompt";
    case "options":
      return "Options";
    case "answer_key":
      return "Answer key";
    case "rationales":
      return "Rationales";
    case "order":
      return "Order";
    default: {
      const _never: never = field;
      return _never;
    }
  }
}

function kindLabel(kind: ChangeKind): string {
  switch (kind) {
    case "curriculum_alignment":
      return "Curriculum alignment";
    case "factual_accuracy":
      return "Factual accuracy";
    case "pedagogy":
      return "Pedagogy";
    case "structure":
      return "Structure";
    case "assessment_validity":
      return "Assessment validity";
    case "answer_key":
      return "Answer key";
    case "accessibility":
      return "Accessibility";
    case "other":
      return "Other";
    default: {
      const _never: never = kind;
      return _never;
    }
  }
}

export function TopicGenerationReview({
  workspace,
  busy,
  canClose,
  onApprove,
  onRequestChanges,
  onAcceptCurrent,
  onRewrite,
  onOverride,
  onDiscard,
}: TopicGenerationReviewProps) {
  const revision = workspace.active_revision;
  if (revision.stage === "qa") {
    return (
      <QaReviewBoard
        workspace={workspace}
        revision={revision}
        busy={busy}
        canClose={canClose}
        onApprove={onApprove}
        onRequestChanges={onRequestChanges}
        onAcceptCurrent={onAcceptCurrent}
        onRewrite={onRewrite}
        onDiscard={onDiscard}
      />
    );
  }
  if (revision.stage === "outline") {
    return (
      <OutlineReviewBoard
        workspace={workspace}
        revision={revision}
        busy={busy}
        canClose={canClose}
        onApprove={onApprove}
        onRequestChanges={onRequestChanges}
        onAcceptCurrent={onAcceptCurrent}
        onRewrite={onRewrite}
        onOverride={onOverride}
        onDiscard={onDiscard}
      />
    );
  }
  const _never: never = revision;
  return _never;
}

type OutlineReviewBoardProps = {
  workspace: ReviewWorkspace;
  revision: OutlineRevision;
  busy: boolean;
  canClose: boolean;
  onApprove: () => void;
  onRequestChanges: (requests: DraftChangeRequest[]) => void;
  onAcceptCurrent: () => void;
  onRewrite: () => void;
  onOverride: (rationale: string) => void;
  onDiscard: () => void;
};

type OutlineTreeEntry = {
  node: OutlineNodeSnapshot;
  children: OutlineTreeEntry[];
};

function outlineTree(nodes: OutlineNodeSnapshot[]): OutlineTreeEntry[] {
  const byParent = new Map<string | null, OutlineNodeSnapshot[]>();
  for (const node of nodes) {
    const siblings = byParent.get(node.parent_node_key) ?? [];
    siblings.push(node);
    byParent.set(node.parent_node_key, siblings);
  }
  const build = (parentKey: string | null): OutlineTreeEntry[] =>
    (byParent.get(parentKey) ?? [])
      .sort((left, right) => left.sequence - right.sequence)
      .map((node) => ({ node, children: build(node.node_key) }));
  return build(null);
}

function questionCount(entry: OutlineTreeEntry): number {
  if (entry.children.length === 0) return entry.node.quota;
  return entry.children.reduce((sum, child) => sum + questionCount(child), 0);
}

function OutlineTreeNode({
  entry,
  workspace,
}: {
  entry: OutlineTreeEntry;
  workspace: ReviewWorkspace;
}) {
  const { node, children } = entry;
  const threads = requestsForNode(workspace, node.node_key);
  const questions = questionCount(entry);
  return (
    <li className="outline-review__node">
      <div className="outline-review__node-head">
        <p className="outline-review__sequence">
          Sequence {node.sequence}: {node.title}
        </p>
      </div>
      <p className="muted">
        {node.token_mass} tokens · {questions} {questions === 1 ? "question" : "questions"}
      </p>
      {node.proposed_outcomes.length > 0 ? (
        <ul className="outline-review__outcomes">
          {node.proposed_outcomes.map((outcome) => (
            <li key={outcome}>{outcome}</li>
          ))}
        </ul>
      ) : null}
      {threads.map((thread) => (
        <p key={thread.change_request.id} className="outline-review__request">
          {thread.change_request.author.display_name} · {kindLabel(thread.change_request.kind)} ·{" "}
          {fieldLabel(thread.change_request.field)}: {thread.change_request.comment}
        </p>
      ))}
      {children.length > 0 ? (
        <ul className="outline-review__children">
          {children.map((child) => (
            <OutlineTreeNode key={child.node.node_key} entry={child} workspace={workspace} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function OutlineReviewBoard({
  workspace,
  revision,
  busy,
  canClose,
  onApprove,
  onRequestChanges,
  onAcceptCurrent,
  onRewrite,
  onOverride,
  onDiscard,
}: OutlineReviewBoardProps) {
  const nodes = useMemo(
    () => [...revision.snapshot.nodes].sort((a, b) => a.sequence - b.sequence),
    [revision.snapshot.nodes],
  );
  const [selectedNodeKey, setSelectedNodeKey] = useState(nodes[0]?.node_key ?? "");
  const [field, setField] = useState<OutlineField>("proposed_outcomes");
  const [kind, setKind] = useState<ChangeKind>("curriculum_alignment");
  const [comment, setComment] = useState("");
  const [drafts, setDrafts] = useState<DraftChangeRequest[]>([]);
  const [overrideRationale, setOverrideRationale] = useState("");
  const openRound = workspace.open_round;
  const canDecide = !canClose && openRound != null && !workspace.published_locked;
  const requestCount = workspace.request_threads.length;
  const canRewrite = canClose && openRound != null && openRound.number === 1 && requestCount > 0;

  function addDraft(node: OutlineNodeSnapshot | null) {
    const trimmed = comment.trim();
    if (!trimmed) return;
    const next: DraftChangeRequest =
      field === "outline_structure" || node == null
        ? {
            target: { kind: "outline_document" },
            field: "outline_structure",
            kind,
            comment: trimmed,
          }
        : {
            target: { kind: "outline_node", node_key: node.node_key },
            field,
            kind,
            comment: trimmed,
          };
    setDrafts((current) => [...current, next]);
    setComment("");
  }

  return (
    <div className="outline-review">
      <p className="outline-review__meta">
        Frozen revision {revision.number}
        {openRound
          ? ` · Teacher round ${openRound.number} of 2 · Due ${new Date(openRound.due_at).toLocaleDateString()}`
          : " · No open teacher round"}
      </p>
      {workspace.reviewer_states.length > 0 ? (
        <ul className="outline-review__roster">
          {workspace.reviewer_states.map((item) => (
            <li key={item.reviewer_user_id}>
              {item.display_name}: {reviewerStateLabel(item.state)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No assigned teachers on this offering yet. An administrator can still close.</p>
      )}
      <ul className="outline-review__nodes">
        {outlineTree(nodes).map((entry) => (
          <OutlineTreeNode key={entry.node.node_key} entry={entry} workspace={workspace} />
        ))}
      </ul>
      {canDecide ? (
        <div className="outline-review__composer">
          <p className="outline-review__sequence">Request a change</p>
          <div className="form__field">
            <label className="form__label" htmlFor="review-node">
              Node
            </label>
            <select
              id="review-node"
              className="form__input"
              value={selectedNodeKey}
              disabled={busy}
              onChange={(event) => setSelectedNodeKey(event.target.value)}
            >
              {nodes.map((node) => (
                <option key={node.node_key} value={node.node_key}>
                  {node.parent_node_key === null ? node.title : `— ${node.title}`}
                </option>
              ))}
            </select>
          </div>
          <div className="form__field">
            <label className="form__label" htmlFor="review-field">
              Field
            </label>
            <select
              id="review-field"
              className="form__input"
              value={field}
              disabled={busy}
              onChange={(event) => setField(event.target.value as OutlineField)}
            >
              {OUTLINE_FIELDS.map((item) => (
                <option key={item} value={item}>
                  {fieldLabel(item)}
                </option>
              ))}
            </select>
          </div>
          <div className="form__field">
            <label className="form__label" htmlFor="review-kind">
              Kind
            </label>
            <select
              id="review-kind"
              className="form__input"
              value={kind}
              disabled={busy}
              onChange={(event) => setKind(event.target.value as ChangeKind)}
            >
              {CHANGE_KINDS.map((item) => (
                <option key={item} value={item}>
                  {kindLabel(item)}
                </option>
              ))}
            </select>
          </div>
          <div className="form__field">
            <label className="form__label" htmlFor="review-comment">
              Comment
            </label>
            <textarea
              id="review-comment"
              className="form__input"
              rows={3}
              value={comment}
              disabled={busy}
              onChange={(event) => setComment(event.target.value)}
            />
          </div>
          <PushButton
            type="button"
            variant="outline"
            disabled={busy || comment.trim() === ""}
            onClick={() => addDraft(nodes.find((node) => node.node_key === selectedNodeKey) ?? null)}
          >
            Add request
          </PushButton>
          {canDecide ? (
            <GenerationReviewAssistant
              runId={workspace.run_id}
              revisionId={revision.id}
              target={
                field === "outline_structure"
                  ? { kind: "outline_document" }
                  : { kind: "outline_node", node_key: selectedNodeKey }
              }
              disabled={busy}
              onInsertDraft={(draft) => setDrafts((current) => [...current, draft])}
            />
          ) : null}
          {drafts.length > 0 ? (
            <ul className="outline-review__drafts">
              {drafts.map((item, index) => (
                <li key={`${item.comment}-${index}`}>{item.comment}</li>
              ))}
            </ul>
          ) : null}
          <div className="admin-upload__actions">
            <PushButton type="button" disabled={busy} loading={busy} onClick={onApprove}>
              Approve outline
            </PushButton>
            <PushButton
              type="button"
              variant="outline"
              disabled={busy || drafts.length === 0}
              onClick={() => onRequestChanges(drafts)}
            >
              Submit change requests
            </PushButton>
          </div>
        </div>
      ) : null}
      {canClose && openRound != null ? (
        <div className="outline-review__closer">
          <div className="admin-upload__actions">
            <PushButton type="button" disabled={busy} loading={busy} onClick={onAcceptCurrent}>
              Accept current outline
            </PushButton>
            {canRewrite ? (
              <PushButton type="button" variant="outline" disabled={busy} onClick={onRewrite}>
                Rewrite from requests
              </PushButton>
            ) : null}
            <PushButton type="button" variant="outline" disabled={busy} onClick={onDiscard}>
              Discard run
            </PushButton>
          </div>
          <div className="form__field">
            <label className="form__label" htmlFor="override-rationale">
              Override rationale
            </label>
            <textarea
              id="override-rationale"
              className="form__input"
              rows={3}
              value={overrideRationale}
              disabled={busy}
              onChange={(event) => setOverrideRationale(event.target.value)}
            />
          </div>
          <PushButton
            type="button"
            variant="outline"
            disabled={busy || overrideRationale.trim() === ""}
            onClick={() => onOverride(overrideRationale.trim())}
          >
            Override and accept
          </PushButton>
        </div>
      ) : null}
    </div>
  );
}

type QaReviewBoardProps = {
  workspace: ReviewWorkspace;
  revision: ContentRevision;
  busy: boolean;
  canClose: boolean;
  onApprove: () => void;
  onRequestChanges: (requests: DraftChangeRequest[]) => void;
  onAcceptCurrent: () => void;
  onRewrite: () => void;
  onDiscard: () => void;
};

type QaTargetKind = "lesson_section" | "quiz_item";

function QaReviewBoard({
  workspace,
  revision,
  busy,
  canClose,
  onApprove,
  onRequestChanges,
  onAcceptCurrent,
  onRewrite,
  onDiscard,
}: QaReviewBoardProps) {
  const sections = useMemo(
    () => [...revision.snapshot.lesson_sections].sort((a, b) => a.sequence - b.sequence),
    [revision.snapshot.lesson_sections],
  );
  const items = useMemo(
    () => [...revision.snapshot.quiz_items].sort((a, b) => a.sequence - b.sequence),
    [revision.snapshot.quiz_items],
  );
  const [targetKind, setTargetKind] = useState<QaTargetKind>("lesson_section");
  const [selectedSectionKey, setSelectedSectionKey] = useState(sections[0]?.section_key ?? "");
  const [selectedItemKey, setSelectedItemKey] = useState(items[0]?.item_key ?? "");
  const [lessonField, setLessonField] = useState<LessonField>("body");
  const [quizField, setQuizField] = useState<QuizField>("whole_item");
  const [kind, setKind] = useState<ChangeKind>("factual_accuracy");
  const [comment, setComment] = useState("");
  const [drafts, setDrafts] = useState<DraftChangeRequest[]>([]);
  const openRound = workspace.open_round;
  const canDecide = !canClose && openRound != null && !workspace.published_locked;
  const requestCount = workspace.request_threads.length;
  const canRewrite = canClose && openRound != null && openRound.number === 1 && requestCount > 0;

  function addDraft() {
    const trimmed = comment.trim();
    if (!trimmed) return;
    const next: DraftChangeRequest =
      targetKind === "lesson_section"
        ? {
            target: { kind: "lesson_section", section_key: selectedSectionKey },
            field: lessonField,
            kind,
            comment: trimmed,
          }
        : {
            target: { kind: "quiz_item", item_key: selectedItemKey },
            field: quizField,
            kind,
            comment: trimmed,
          };
    setDrafts((current) => [...current, next]);
    setComment("");
  }

  return (
    <div className="outline-review">
      <p className="outline-review__meta">
        Frozen content revision {revision.number}
        {openRound
          ? ` · Teacher round ${openRound.number} of 2 · Due ${new Date(openRound.due_at).toLocaleDateString()}`
          : " · No open teacher round"}
      </p>
      {workspace.reviewer_states.length > 0 ? (
        <ul className="outline-review__roster">
          {workspace.reviewer_states.map((item) => (
            <li key={item.reviewer_user_id}>
              {item.display_name}: {reviewerStateLabel(item.state)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">
          No assigned teachers on this offering yet. An administrator can still close.
        </p>
      )}
      <ul className="outline-review__nodes">
        {sections.map((section) => {
          const threads = requestsForSection(workspace, section.section_key);
          return (
            <li key={section.section_key} className="outline-review__node">
              <p className="outline-review__sequence">
                Section {section.sequence}: {section.heading}
              </p>
              <p className="muted">{section.markdown.slice(0, 180)}</p>
              {threads.map((thread) => (
                <p key={thread.change_request.id} className="outline-review__request">
                  {thread.change_request.author.display_name} ·{" "}
                  {kindLabel(thread.change_request.kind)} ·{" "}
                  {fieldLabel(thread.change_request.field)}: {thread.change_request.comment}
                </p>
              ))}
            </li>
          );
        })}
        {items.map((item) => {
          const threads = requestsForItem(workspace, item.item_key);
          return (
            <li key={item.item_key} className="outline-review__node">
              <p className="outline-review__sequence">
                Item {item.sequence}:{" "}
                <MarkdownContent inline>{presentQuizText(item.prompt)}</MarkdownContent>
              </p>
              <p className="muted">
                Correct {item.correct_label}.{" "}
                <MarkdownContent inline>{presentQuizText(item.correct_rationale)}</MarkdownContent>
              </p>
              {threads.map((thread) => (
                <p key={thread.change_request.id} className="outline-review__request">
                  {thread.change_request.author.display_name} ·{" "}
                  {kindLabel(thread.change_request.kind)} ·{" "}
                  {fieldLabel(thread.change_request.field)}: {thread.change_request.comment}
                </p>
              ))}
            </li>
          );
        })}
      </ul>
      {canDecide ? (
        <div className="outline-review__composer">
          <p className="outline-review__sequence">Request a change</p>
          <div className="form__field">
            <label className="form__label" htmlFor="qa-target-kind">
              Target
            </label>
            <select
              id="qa-target-kind"
              className="form__input"
              value={targetKind}
              disabled={busy}
              onChange={(event) => {
                const value = event.target.value;
                switch (value) {
                  case "lesson_section":
                  case "quiz_item":
                    setTargetKind(value);
                    return;
                  default: {
                    const _never: never = value as never;
                    void _never;
                  }
                }
              }}
            >
              <option value="lesson_section">Lesson section</option>
              <option value="quiz_item">Quiz item</option>
            </select>
          </div>
          {targetKind === "lesson_section" ? (
            <>
              <div className="form__field">
                <label className="form__label" htmlFor="qa-section">
                  Section
                </label>
                <select
                  id="qa-section"
                  className="form__input"
                  value={selectedSectionKey}
                  disabled={busy}
                  onChange={(event) => setSelectedSectionKey(event.target.value)}
                >
                  {sections.map((section) => (
                    <option key={section.section_key} value={section.section_key}>
                      {section.heading}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form__field">
                <label className="form__label" htmlFor="qa-section-field">
                  Field
                </label>
                <select
                  id="qa-section-field"
                  className="form__input"
                  value={lessonField}
                  disabled={busy}
                  onChange={(event) => setLessonField(event.target.value as LessonField)}
                >
                  {LESSON_FIELDS.map((item) => (
                    <option key={item} value={item}>
                      {fieldLabel(item)}
                    </option>
                  ))}
                </select>
              </div>
            </>
          ) : (
            <>
              <div className="form__field">
                <label className="form__label" htmlFor="qa-item">
                  Item
                </label>
                <select
                  id="qa-item"
                  className="form__input"
                  value={selectedItemKey}
                  disabled={busy}
                  onChange={(event) => setSelectedItemKey(event.target.value)}
                >
                  {items.map((item) => (
                    <option key={item.item_key} value={item.item_key}>
                      {item.sequence}. {item.prompt.slice(0, 80)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form__field">
                <label className="form__label" htmlFor="qa-item-field">
                  Field
                </label>
                <select
                  id="qa-item-field"
                  className="form__input"
                  value={quizField}
                  disabled={busy}
                  onChange={(event) => setQuizField(event.target.value as QuizField)}
                >
                  {QUIZ_FIELDS.map((item) => (
                    <option key={item} value={item}>
                      {fieldLabel(item)}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}
          <div className="form__field">
            <label className="form__label" htmlFor="qa-kind">
              Kind
            </label>
            <select
              id="qa-kind"
              className="form__input"
              value={kind}
              disabled={busy}
              onChange={(event) => setKind(event.target.value as ChangeKind)}
            >
              {QA_CHANGE_KINDS.map((item) => (
                <option key={item} value={item}>
                  {kindLabel(item)}
                </option>
              ))}
            </select>
          </div>
          <div className="form__field">
            <label className="form__label" htmlFor="qa-comment">
              Comment
            </label>
            <textarea
              id="qa-comment"
              className="form__input"
              rows={3}
              value={comment}
              disabled={busy}
              onChange={(event) => setComment(event.target.value)}
            />
          </div>
          <PushButton
            type="button"
            variant="outline"
            disabled={busy || comment.trim() === ""}
            onClick={addDraft}
          >
            Add request
          </PushButton>
          {canDecide ? (
            <GenerationReviewAssistant
              runId={workspace.run_id}
              revisionId={revision.id}
              target={
                targetKind === "lesson_section"
                  ? { kind: "lesson_section", section_key: selectedSectionKey }
                  : { kind: "quiz_item", item_key: selectedItemKey }
              }
              disabled={busy}
              onInsertDraft={(draft) => setDrafts((current) => [...current, draft])}
            />
          ) : null}
          {drafts.length > 0 ? (
            <ul className="outline-review__drafts">
              {drafts.map((item, index) => (
                <li key={`${item.comment}-${index}`}>{item.comment}</li>
              ))}
            </ul>
          ) : null}
          <div className="admin-upload__actions">
            <PushButton type="button" disabled={busy} loading={busy} onClick={onApprove}>
              Approve content
            </PushButton>
            <PushButton
              type="button"
              variant="outline"
              disabled={busy || drafts.length === 0}
              onClick={() => onRequestChanges(drafts)}
            >
              Submit change requests
            </PushButton>
          </div>
        </div>
      ) : null}
      {canClose && openRound != null ? (
        <div className="outline-review__closer">
          <div className="admin-upload__actions">
            <PushButton type="button" disabled={busy} loading={busy} onClick={onAcceptCurrent}>
              Accept current content
            </PushButton>
            {canRewrite ? (
              <PushButton type="button" variant="outline" disabled={busy} onClick={onRewrite}>
                Rewrite from requests
              </PushButton>
            ) : null}
            <PushButton type="button" variant="outline" disabled={busy} onClick={onDiscard}>
              Discard run
            </PushButton>
          </div>
        </div>
      ) : null}
    </div>
  );
}
