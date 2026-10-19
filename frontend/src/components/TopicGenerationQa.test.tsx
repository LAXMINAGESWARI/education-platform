import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { GenerationQaItem } from "../api/types";
import { TopicGenerationQa } from "./TopicGenerationQa";

function item(over: Partial<GenerationQaItem> = {}): GenerationQaItem {
  return {
    question_id: "q-1",
    question_version_id: "qv-1",
    prompt: "What is 1/2 + 1/2?",
    options: { A: "1", B: "1/2", C: "2", D: "0" },
    correct_label: "A",
    correct_rationale: "Two halves make a whole.",
    distractor_rationales: { B: "That is only one half.", C: "Too large.", D: "Too small." },
    subtopic_id: "st-1",
    sequence: 1,
    bloom: "apply",
    misconception_labels: ["two halves make one"],
    ...over,
  };
}

describe("TopicGenerationQa", () => {
  it("shows the draft lesson and quiz side by side without hiding tabs", () => {
    render(
      <TopicGenerationQa
        items={[item(), item({ question_id: "q-2", sequence: 2, prompt: "What is 1/4 + 1/4?" })]}
        draftLessonMarkdown={"## Slide 1 — Fractions\n\nTwo halves make one."}
        phase="qa_review"
        busy={false}
        canPublish
        canReject
        onRejectSelected={vi.fn()}
        onRequestPublish={vi.fn()}
      />,
    );

    expect(screen.queryByRole("tab", { name: "Lesson" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Quiz" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Draft lesson" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Draft quiz" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Fractions/ })).toBeInTheDocument();
    expect(screen.getByText("Two halves make one.")).toBeInTheDocument();
    expect(screen.getByText("What is 1/2 + 1/2?")).toBeInTheDocument();
    expect(screen.getByText("What is 1/4 + 1/4?")).toBeInTheDocument();
    expect(screen.getAllByText("Apply").length).toBeGreaterThan(0);
    expect(screen.getAllByText("two halves make one").length).toBeGreaterThan(0);
    expect(screen.getAllByText("A").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Two halves make a whole.").length).toBeGreaterThan(0);
    expect(screen.getAllByText("That is only one half.").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Publish to students" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reject selected items" })).toBeDisabled();
  });

  it("publishes from qa_review after the publish button is clicked", async () => {
    const user = userEvent.setup();
    const onRequestPublish = vi.fn();
    render(
      <TopicGenerationQa
        items={[item()]}
        draftLessonMarkdown="# Lesson"
        phase="qa_review"
        busy={false}
        canPublish
        canReject
        onRejectSelected={vi.fn()}
        onRequestPublish={onRequestPublish}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Publish to students" }));
    expect(onRequestPublish).toHaveBeenCalledTimes(1);
  });

  it("rejects only the selected items", async () => {
    const user = userEvent.setup();
    const onRejectSelected = vi.fn();
    render(
      <TopicGenerationQa
        items={[
          item(),
          item({ question_id: "q-2", sequence: 2, prompt: "Second prompt" }),
        ]}
        draftLessonMarkdown="# Lesson"
        phase="qa_review"
        busy={false}
        canPublish
        canReject
        onRejectSelected={onRejectSelected}
        onRequestPublish={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: "Select item 2" }));
    await user.click(screen.getByRole("button", { name: "Reject selected items" }));
    expect(onRejectSelected).toHaveBeenCalledWith(["q-2"]);
  });

  it("hides publish and reject after the run is published", () => {
    render(
      <TopicGenerationQa
        items={[item()]}
        draftLessonMarkdown="# Lesson"
        publishedLessonVersionId="lesson-v1"
        publishedQuizVersionId="quiz-v1"
        phase="published"
        busy={false}
        canPublish={false}
        canReject={false}
        onRejectSelected={vi.fn()}
        onRequestPublish={vi.fn()}
      />,
    );

    expect(
      screen.getByText("This run is published. Students can open the topic lesson and topic quiz."),
    ).toBeInTheDocument();
    expect(screen.getByText(/Published lesson lesson-v1/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Lesson/ })).toBeInTheDocument();
    expect(screen.getByText("What is 1/2 + 1/2?")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish to students" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject selected items" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Select item 1" })).not.toBeInTheDocument();
  });

  it("hides closer actions when the viewer cannot publish", () => {
    render(
      <TopicGenerationQa
        items={[item()]}
        draftLessonMarkdown="# Lesson"
        phase="qa_review"
        busy={false}
        canPublish={false}
        canReject={false}
        onRejectSelected={vi.fn()}
        onRequestPublish={vi.fn()}
      />,
    );

    expect(screen.getByText("What is 1/2 + 1/2?")).toBeInTheDocument();
    expect(screen.getByText("A")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish to students" })).not.toBeInTheDocument();
  });

  it("shows a key-disputed badge and keeps reject available", async () => {
    const user = userEvent.setup();
    const onRejectSelected = vi.fn();
    render(
      <TopicGenerationQa
        items={[
          item({ key_disputed: false, blind_answer: "A" }),
          item({
            question_id: "q-2",
            sequence: 2,
            prompt: "Which fraction is larger?",
            key_disputed: true,
            blind_answer: "C",
          }),
          item({
            question_id: "q-3",
            sequence: 3,
            prompt: "Solver errored",
            key_disputed: false,
            blind_answer: null,
          }),
        ]}
        draftLessonMarkdown="# Lesson"
        phase="qa_review"
        busy={false}
        canPublish
        canReject
        onRejectSelected={onRejectSelected}
        onRequestPublish={vi.fn()}
      />,
    );

    expect(screen.getByText("Key disputed - solver chose C")).toBeInTheDocument();
    expect(screen.getAllByText(/Key disputed/)).toHaveLength(1);
    expect(screen.queryByText("Key disputed - solver chose A")).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Select item 2" }));
    await user.click(screen.getByRole("button", { name: "Reject selected items" }));
    expect(onRejectSelected).toHaveBeenCalledWith(["q-2"]);
  });
});
