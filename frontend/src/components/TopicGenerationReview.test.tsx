import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { askGenerationAssistant } from "../api/generation";
import type { ReviewWorkspace } from "../api/types";
import { TopicGenerationReview } from "./TopicGenerationReview";

vi.mock("../api/generation", () => ({
  askGenerationAssistant: vi.fn(),
}));

function workspace(over: Partial<ReviewWorkspace> = {}): ReviewWorkspace {
  return {
    run_id: "run-1",
    topic_id: "topic-1",
    phase: "outline_review",
    published_locked: false,
    viewer_is_closer: false,
    active_revision: {
      stage: "outline",
      id: "rev-1",
      run_id: "run-1",
      number: 1,
      parent_id: null,
      origin: "initial_generation",
      snapshot_hash: "abc",
      created_at: "2026-09-13T00:00:00Z",
      created_by: { display_name: "outline", occurred_at: "2026-09-13T00:00:00Z" },
      snapshot: {
        target_item_count: 80,
        nodes: [
          {
            node_key: "node-1",
            parent_node_key: null,
            slug: "fractions",
            title: "Fractions",
            token_mass: 120,
            prerequisite_score: "0.10",
            centrality: "0.40",
            weight: "0.5000",
            matched_subtopic_id: null,
            force_create: false,
            proposed_outcomes: ["Add like fractions"],
            sequence: 1,
            quota: 12,
          },
        ],
      },
    },
    diff_from_parent: null,
    open_round: {
      id: "round-1",
      run_id: "run-1",
      revision_id: "rev-1",
      stage: "outline",
      number: 1,
      opened_at: "2026-09-13T00:00:00Z",
      due_at: "2026-09-16T00:00:00Z",
      sealed_at: null,
    },
    reviewer_states: [],
    request_threads: [],
    history: [],
    ...over,
  };
}

describe("TopicGenerationReview", () => {
  it("lets a teacher approve without closer actions", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(
      <TopicGenerationReview
        workspace={workspace()}
        busy={false}
        canClose={false}
        onApprove={onApprove}
        onRequestChanges={vi.fn()}
        onAcceptCurrent={vi.fn()}
        onRewrite={vi.fn()}
        onOverride={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );

    expect(screen.getByText("Sequence 1: Fractions")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept current outline" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Override and accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discard run" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve outline" }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("nests a subheading under its section with outcomes and a rolled-up question count", () => {
    const base = workspace();
    const revision = base.active_revision;
    if (revision === null || revision.stage !== "outline") {
      throw new Error("fixture");
    }
    render(
      <TopicGenerationReview
        workspace={workspace({
          active_revision: {
            ...revision,
            snapshot: {
              target_item_count: 80,
              nodes: [
                {
                  node_key: "section",
                  parent_node_key: null,
                  slug: "properties",
                  title: "6.1 Some Properties of Multiplication",
                  token_mass: 0,
                  prerequisite_score: "0",
                  centrality: "0",
                  weight: "0",
                  matched_subtopic_id: null,
                  force_create: false,
                  proposed_outcomes: ["Use the distributive property"],
                  sequence: 1,
                  quota: 0,
                },
                {
                  node_key: "increments",
                  parent_node_key: "section",
                  slug: "increments",
                  title: "Increments in Products",
                  token_mass: 171,
                  prerequisite_score: "0.50",
                  centrality: "0.80",
                  weight: "0.4000",
                  matched_subtopic_id: null,
                  force_create: false,
                  proposed_outcomes: ["Grow a product by a small increment"],
                  sequence: 2,
                  quota: 18,
                },
              ],
            },
          },
        })}
        busy={false}
        canClose={false}
        onApprove={vi.fn()}
        onRequestChanges={vi.fn()}
        onAcceptCurrent={vi.fn()}
        onRewrite={vi.fn()}
        onOverride={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );

    const section = screen.getByText("Sequence 1: 6.1 Some Properties of Multiplication");
    const child = screen.getByText("Sequence 2: Increments in Products");
    expect(section.closest("li")?.querySelector(".outline-review__children")).toContainElement(child);
    expect(screen.getByText("Use the distributive property")).toBeInTheDocument();
    expect(screen.getByText("Grow a product by a small increment")).toBeInTheDocument();
    expect(screen.getByText("0 tokens · 18 questions")).toBeInTheDocument();
    expect(screen.getByText("171 tokens · 18 questions")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "— Increments in Products" })).toBeInTheDocument();
  });

  it("lets an administrator close with accept, override, or discard", async () => {
    const user = userEvent.setup();
    const onOverride = vi.fn();
    render(
      <TopicGenerationReview
        workspace={workspace({ viewer_is_closer: true })}
        busy={false}
        canClose
        onApprove={vi.fn()}
        onRequestChanges={vi.fn()}
        onAcceptCurrent={vi.fn()}
        onRewrite={vi.fn()}
        onOverride={onOverride}
        onDiscard={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Accept current outline" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard run" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve outline" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Override and accept" })).toBeDisabled();
    await user.type(screen.getByLabelText("Override rationale"), "Keep the current tree.");
    expect(screen.getByRole("button", { name: "Override and accept" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Override and accept" }));
    expect(onOverride).toHaveBeenCalledWith("Keep the current tree.");
  });

  it("inserts an assistant draft without submitting the decision", async () => {
    const user = userEvent.setup();
    const onRequestChanges = vi.fn();
    vi.mocked(askGenerationAssistant).mockResolvedValue({
      content: "The outcome is too broad.",
      citations: [],
      draft_change_request: {
        target: { kind: "outline_node", node_key: "node-1" },
        field: "proposed_outcomes",
        kind: "curriculum_alignment",
        comment: "Add an outcome for variables on both sides.",
      },
    });
    render(
      <TopicGenerationReview
        workspace={workspace()}
        busy={false}
        canClose={false}
        onApprove={vi.fn()}
        onRequestChanges={onRequestChanges}
        onAcceptCurrent={vi.fn()}
        onRewrite={vi.fn()}
        onOverride={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );

    await user.type(
      screen.getByLabelText("Assistant question"),
      "Draft a tighter outcome for this node.",
    );
    await user.click(screen.getByRole("button", { name: "Ask assistant" }));
    await waitFor(() => {
      expect(askGenerationAssistant).toHaveBeenCalledWith("run-1", {
        revision_id: "rev-1",
        target: { kind: "outline_node", node_key: "node-1" },
        message: "Draft a tighter outcome for this node.",
      });
    });
    expect(onRequestChanges).not.toHaveBeenCalled();
    expect(screen.getByText("Add an outcome for variables on both sides.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Submit change requests" }));
    expect(onRequestChanges).toHaveBeenCalledWith([
      {
        target: { kind: "outline_node", node_key: "node-1" },
        field: "proposed_outcomes",
        kind: "curriculum_alignment",
        comment: "Add an outcome for variables on both sides.",
      },
    ]);
  });

  it("lets a teacher request section and item changes", async () => {
    const user = userEvent.setup();
    const onRequestChanges = vi.fn();
    const qaWorkspace = workspace({
      phase: "qa_review",
      active_revision: {
        stage: "qa",
        id: "rev-qa",
        run_id: "run-1",
        number: 1,
        parent_id: null,
        origin: "initial_generation",
        snapshot_hash: "def",
        created_at: "2026-09-13T00:00:00Z",
        created_by: { display_name: "lesson", occurred_at: "2026-09-13T00:00:00Z" },
        snapshot: {
          rendered_lesson_markdown: "# Fractions",
          quiz_version_id: "quiz-1",
          lesson_sections: [
            {
              section_key: "sec-1",
              heading: "Like fractions",
              markdown: "**The idea.** Add the numerators.",
              sequence: 1,
            },
          ],
          quiz_items: [
            {
              item_key: "item-1",
              question_id: "q-1",
              question_version_id: "qv-1",
              subtopic_id: "st-1",
              prompt: "What is 1/2 + 1/2?",
              options: { A: "1", B: "2" },
              correct_label: "A",
              correct_rationale: "Two halves make a whole.",
              distractor_rationales: { B: "Too large." },
              sequence: 1,
            },
          ],
        },
      },
      open_round: {
        id: "round-qa",
        run_id: "run-1",
        revision_id: "rev-qa",
        stage: "qa",
        number: 1,
        opened_at: "2026-09-13T00:00:00Z",
        due_at: "2026-09-16T00:00:00Z",
        sealed_at: null,
      },
    });
    render(
      <TopicGenerationReview
        workspace={qaWorkspace}
        busy={false}
        canClose={false}
        onApprove={vi.fn()}
        onRequestChanges={onRequestChanges}
        onAcceptCurrent={vi.fn()}
        onRewrite={vi.fn()}
        onOverride={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );

    expect(screen.getByText("Section 1: Like fractions")).toBeInTheDocument();
    expect(
      screen.getByText((_content, element) => {
        return element?.textContent === "Item 1: What is 1/2 + 1/2?";
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept current outline" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Comment"), "Clarify the recap.");
    await user.click(screen.getByRole("button", { name: "Add request" }));
    await user.click(screen.getByRole("button", { name: "Submit change requests" }));
    expect(onRequestChanges).toHaveBeenCalledWith([
      {
        target: { kind: "lesson_section", section_key: "sec-1" },
        field: "body",
        kind: "factual_accuracy",
        comment: "Clarify the recap.",
      },
    ]);
  });

  it("lets an administrator accept or discard QA content without override", () => {
    const qaWorkspace = workspace({
      phase: "qa_review",
      viewer_is_closer: true,
      active_revision: {
        stage: "qa",
        id: "rev-qa",
        run_id: "run-1",
        number: 1,
        parent_id: null,
        origin: "initial_generation",
        snapshot_hash: "def",
        created_at: "2026-09-13T00:00:00Z",
        created_by: { display_name: "lesson", occurred_at: "2026-09-13T00:00:00Z" },
        snapshot: {
          rendered_lesson_markdown: "# Fractions",
          quiz_version_id: "quiz-1",
          lesson_sections: [
            {
              section_key: "sec-1",
              heading: "Like fractions",
              markdown: "**The idea.** Add the numerators.",
              sequence: 1,
            },
          ],
          quiz_items: [
            {
              item_key: "item-1",
              question_id: "q-1",
              question_version_id: "qv-1",
              subtopic_id: "st-1",
              prompt: "What is 1/2 + 1/2?",
              options: { A: "1", B: "2" },
              correct_label: "A",
              correct_rationale: "Two halves make a whole.",
              distractor_rationales: { B: "Too large." },
              sequence: 1,
            },
          ],
        },
      },
      open_round: {
        id: "round-qa",
        run_id: "run-1",
        revision_id: "rev-qa",
        stage: "qa",
        number: 1,
        opened_at: "2026-09-13T00:00:00Z",
        due_at: "2026-09-16T00:00:00Z",
        sealed_at: null,
      },
    });
    render(
      <TopicGenerationReview
        workspace={qaWorkspace}
        busy={false}
        canClose
        onApprove={vi.fn()}
        onRequestChanges={vi.fn()}
        onAcceptCurrent={vi.fn()}
        onRewrite={vi.fn()}
        onOverride={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Accept current content" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard run" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Override and accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve content" })).not.toBeInTheDocument();
  });
});
