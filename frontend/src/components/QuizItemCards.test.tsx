import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { GenerationQaItem } from "../api/types";
import { QuizItemCards } from "./QuizItemCards";

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    parse: vi.fn(async () => ({ diagramType: "flowchart" })),
    render: vi.fn(async () => ({
      svg: '<svg xmlns="http://www.w3.org/2000/svg"><g /></svg>',
    })),
  },
}));

function item(over: Partial<GenerationQaItem> = {}): GenerationQaItem {
  return {
    question_id: "q-1",
    question_version_id: "qv-1",
    prompt: "What is 1/2 + 1/2?",
    options: { A: "1", B: "1/2", C: "2", D: "0" },
    correct_label: "A",
    correct_rationale: "Two halves make a whole.",
    distractor_rationales: {},
    subtopic_id: "st-1",
    sequence: 1,
    ...over,
  };
}

describe("QuizItemCards", () => {
  it("shows Needs review only when the judge flagged the item", () => {
    const { rerender } = render(
      <QuizItemCards items={[item({ judge_needs_review: true })]} />,
    );
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(screen.getByText("Needs review").className).toContain("badge--warn");

    rerender(<QuizItemCards items={[item({ judge_needs_review: false })]} />);
    expect(screen.queryByText("Needs review")).not.toBeInTheDocument();

    rerender(<QuizItemCards items={[item()]} />);
    expect(screen.queryByText("Needs review")).not.toBeInTheDocument();
  });
});
