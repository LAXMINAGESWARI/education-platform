import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MarkdownContent } from "./MarkdownContent";
import { quoteFlowchartNodeLabels } from "./MermaidDiagram";

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    parse: vi.fn(async () => ({ diagramType: "flowchart" })),
    render: vi.fn(async () => ({
      svg: '<svg xmlns="http://www.w3.org/2000/svg"><g /></svg>',
    })),
  },
}));

const MERMAID_FIXTURE = ["Lesson intro", "", "```mermaid", "graph TD", "  A --> B", "```"].join(
  "\n",
);

describe("MarkdownContent", () => {
  it("renders quiz equations, including fractions JSON turned into control characters", () => {
    const { rerender } = render(<MarkdownContent>{"What is $2x - 3 = 7$?"}</MarkdownContent>);
    expect(screen.queryByText(/\$2x/)).not.toBeInTheDocument();
    expect(document.querySelector(".katex")).not.toBeNull();
    rerender(<MarkdownContent>{"Simplify $2 \u000crac{1}{2}$."}</MarkdownContent>);
    expect(document.body.textContent).not.toContain("\u000c");
    expect(document.querySelector(".katex")).not.toBeNull();
  });

  it("renders LaTeX math instead of raw dollar signs", () => {
    render(<MarkdownContent>{"Area is $n^2$ square units."}</MarkdownContent>);
    expect(screen.queryByText(/\$n\^2\$/)).not.toBeInTheDocument();
    expect(document.querySelector(".katex")).not.toBeNull();
    expect(screen.getByText(/Area is/)).toBeInTheDocument();
    expect(screen.getByText(/square units/)).toBeInTheDocument();
  });

  it("renders spaced dollar delimiters from generated lessons", () => {
    render(<MarkdownContent>{"Solve for $x $. Then $$2x = 8 $$."}</MarkdownContent>);
    expect(screen.queryByText(/\$x \$/)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".katex").length).toBeGreaterThan(1);
  });

  it("renders escaped LaTeX delimiters and list display math", () => {
    render(
      <MarkdownContent>
        {[
          String.raw`Solve \(x + 2 = 5\).`,
          "",
          "- Odd sums:",
          "  $$1 + 3 = 4 = 2^2$$",
        ].join("\n")}
      </MarkdownContent>,
    );
    expect(screen.queryByText(/\\\(/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\$\$1 \+ 3/)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".katex").length).toBeGreaterThan(1);
  });

  it("renders double-escaped generated lesson math", () => {
    render(
      <MarkdownContent>{String.raw`Let's look at the equation \\(2x - 3 = 9\\).`}</MarkdownContent>,
    );
    expect(screen.queryByText(/\\\\?\(2x/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\$2x - 3 = 9\$/)).not.toBeInTheDocument();
    expect(document.querySelector(".katex")).not.toBeNull();
  });

  it("keeps surrounding lesson text when one formula is invalid", () => {
    render(<MarkdownContent>{String.raw`Keep going $\notamacro{x}$ after.`}</MarkdownContent>);
    expect(screen.getByText(/Keep going/)).toBeInTheDocument();
    expect(screen.getByText(/after/)).toBeInTheDocument();
  });

  it("renders a mermaid fence as a diagram, not a code listing", async () => {
    render(<MarkdownContent>{MERMAID_FIXTURE}</MarkdownContent>);

    const diagram = await screen.findByRole("img", { name: "Diagram" });
    expect(diagram).toHaveClass("markdown-mermaid");
    expect(diagram).toHaveClass("mermaid");
    expect(screen.getByText("Lesson intro")).toBeInTheDocument();
    expect(screen.queryByText(/graph TD/)).not.toBeInTheDocument();
    expect(document.querySelector("pre code.language-mermaid")).toBeNull();
    await waitFor(() => {
      expect(diagram.querySelector("svg")).not.toBeNull();
    });
  });

  it("quotes flowchart labels that contain parentheses", () => {
    expect(quoteFlowchartNodeLabels("graph TD;\n  A[Left Hand Side (LHS)] --> B[OK]")).toContain(
      'A["Left Hand Side (LHS)"]',
    );
    expect(quoteFlowchartNodeLabels("flowchart TD\n  A[OK] --> B[Also OK]")).toContain("A[OK]");
  });

  it("still renders non-mermaid fences as code", () => {
    render(<MarkdownContent>{"```js\nconst x = 1;\n```"}</MarkdownContent>);
    expect(screen.getByText("const x = 1;")).toBeInTheDocument();
    expect(document.querySelector("pre code")).not.toBeNull();
    expect(screen.queryByRole("img", { name: "Diagram" })).not.toBeInTheDocument();
  });
});
