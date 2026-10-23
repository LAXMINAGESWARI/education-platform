import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProgressReconnectError } from "../../api/progress";
import { TEXT_TO_SQL_STAGE_ORDER } from "../../api/stageOrder";
import * as textToSqlApi from "../../api/textToSql";
import { ApiError } from "../../api/types";
import { RequireAuth } from "../../auth/RequireAuth";
import { RequireRole } from "../../auth/RequireRole";
import { ROLE_TEACHER } from "../../auth/roles";
import { TeacherShell } from "../../components/TeacherShell";
import { AssistantPage } from "./AssistantPage";

vi.mock("../../api/textToSql", async () => {
  const actual = await vi.importActual<typeof import("../../api/textToSql")>("../../api/textToSql");
  return {
    ...actual,
    askQuestion: vi.fn(),
    askQuestionStream: vi.fn(),
  };
});

const authState = vi.hoisted(() => ({
  user: null as null | { id: string; roles: string[]; full_name: string },
  loading: false,
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    user: authState.user,
    loading: authState.loading,
    signOut: vi.fn(),
    isDevMockSession: false,
  }),
}));

const ROUTER_FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true } as const;

function renderPage() {
  return render(
    <MemoryRouter future={ROUTER_FUTURE}>
      <AssistantPage />
    </MemoryRouter>,
  );
}

describe("AssistantPage", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(textToSqlApi.askQuestion).mockReset();
    vi.mocked(textToSqlApi.askQuestionStream).mockReset();
    vi.mocked(textToSqlApi.askQuestionStream).mockRejectedValue(
      new ApiError("Not Found", 404, null),
    );
  });

  it("submitting a question renders a new thread entry with the answer and a visible confidence indicator", async () => {
    vi.mocked(textToSqlApi.askQuestion).mockResolvedValue({
      natural_answer: "You teach 42 students across your sections.",
      confidence: "high",
      provenance: "Queried: student_360.",
    });

    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("Question"), "How many students do I teach?");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    expect(screen.getByText("How many students do I teach?")).toBeInTheDocument();

    await waitFor(() => {
      expect(
        screen.getByText("You teach 42 students across your sections."),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("High confidence")).toBeInTheDocument();
    expect(screen.getByText("High confidence")).toHaveClass("badge--ok");
  });

  it("renders a low-confidence/refusal response as a normal message, not an error state", async () => {
    vi.mocked(textToSqlApi.askQuestion).mockResolvedValue({
      natural_answer: "That question touches data you don't have access to.",
      confidence: "low",
      provenance: null,
    });

    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("Question"), "Show me every student's data");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    await waitFor(() => {
      expect(
        screen.getByText("That question touches data you don't have access to."),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Low confidence")).toBeInTheDocument();
    expect(screen.getByText("Low confidence")).toHaveClass("badge--warn");
    // Not rendered as an error: no alert banner anywhere in the thread.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders an actual HTTP error as a distinct, visibly different error state", async () => {
    vi.mocked(textToSqlApi.askQuestion).mockRejectedValue(
      new ApiError("Something went wrong on our end — please try again.", 500, null),
    );

    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("Question"), "How many students do I teach?");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong — please try again.");
    // The genuine failure never produces a confidence badge or an answer bubble.
    expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
  });

  it("does not bundle the first question/answer into the second request's payload", async () => {
    vi.mocked(textToSqlApi.askQuestion)
      .mockResolvedValueOnce({
        natural_answer: "First answer.",
        confidence: "high",
        provenance: null,
      })
      .mockResolvedValueOnce({
        natural_answer: "Second answer.",
        confidence: "high",
        provenance: null,
      });

    const user = userEvent.setup();
    renderPage();

    await user.type(screen.getByLabelText("Question"), "First question");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("First answer.")).toBeInTheDocument());

    await user.type(screen.getByLabelText("Question"), "Second question");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByText("Second answer.")).toBeInTheDocument());

    // Each call receives only the current question string -- no history parameter exists
    // on askQuestion at all, so there is no way for a prior turn to have been included.
    expect(textToSqlApi.askQuestion).toHaveBeenNthCalledWith(1, "First question");
    expect(textToSqlApi.askQuestion).toHaveBeenNthCalledWith(2, "Second question");
    expect(textToSqlApi.askQuestion).toHaveBeenCalledTimes(2);
  });

  describe("suggested questions dropdown", () => {
    const EXPECTED_QUESTIONS = [
      "How many students do I teach in total?",
      "Which subjects am I assigned to?",
      "What sections am I assigned to?",
      "What is the average attendance rate for my students this term?",
      "Show me the struggling students.",
      "How many of my students are doing well?",
      "How many students have attendance less than 85%?",
      "Who took the last quiz?",
      "What is the average quiz score for my students?",
      "What was the highest quiz score?",
    ];

    it("offers the tested questions, grouped, plus a placeholder", () => {
      renderPage();

      const select = screen.getByLabelText("Suggested questions");
      const options = within(select).getAllByRole("option");
      // placeholder first, then every suggested question in order
      expect(options[0]).toHaveTextContent("Choose a question…");
      expect(options[0]).toBeDisabled();
      expect(options.slice(1).map((option) => option.textContent)).toEqual(EXPECTED_QUESTIONS);
      expect(within(select).getAllByRole("group")).toHaveLength(3);
    });

    it("picking a question fills the box with its exact text and does not send it", async () => {
      const user = userEvent.setup();
      renderPage();

      await user.selectOptions(
        screen.getByLabelText("Suggested questions"),
        "Show me the struggling students.",
      );

      expect(screen.getByLabelText("Question")).toHaveValue("Show me the struggling students.");
      expect(textToSqlApi.askQuestion).not.toHaveBeenCalled();
      // focus moves to the box so the teacher can edit or just press Ask
      expect(screen.getByLabelText("Question")).toHaveFocus();
    });

    it("returns to the placeholder after a pick so the same question can be chosen again", async () => {
      const user = userEvent.setup();
      renderPage();

      const select = screen.getByLabelText("Suggested questions");
      await user.selectOptions(select, "Who took the last quiz?");

      expect(select).toHaveValue("");
    });

    it("sends exactly the picked question when Ask is pressed", async () => {
      vi.mocked(textToSqlApi.askQuestion).mockResolvedValue({
        natural_answer: "The student count is 72.",
        confidence: "high",
        provenance: null,
      });
      const user = userEvent.setup();
      renderPage();

      await user.selectOptions(
        screen.getByLabelText("Suggested questions"),
        "How many students do I teach in total?",
      );
      await user.click(screen.getByRole("button", { name: "Ask" }));

      await waitFor(() => expect(screen.getByText("The student count is 72.")).toBeInTheDocument());
      expect(textToSqlApi.askQuestion).toHaveBeenCalledTimes(1);
      expect(textToSqlApi.askQuestion).toHaveBeenCalledWith("How many students do I teach in total?");
    });

    it("sends the edited text when the teacher changes the picked question first", async () => {
      vi.mocked(textToSqlApi.askQuestion).mockResolvedValue({
        natural_answer: "The student count is 4.",
        confidence: "high",
        provenance: null,
      });
      const user = userEvent.setup();
      renderPage();

      await user.selectOptions(
        screen.getByLabelText("Suggested questions"),
        "How many students have attendance less than 85%?",
      );
      const box = screen.getByLabelText("Question");
      await user.clear(box);
      await user.type(box, "How many students have attendance less than 75%?");
      await user.click(screen.getByRole("button", { name: "Ask" }));

      await waitFor(() => expect(textToSqlApi.askQuestion).toHaveBeenCalledTimes(1));
      expect(textToSqlApi.askQuestion).toHaveBeenCalledWith(
        "How many students have attendance less than 75%?",
      );
    });

    it("leaves free-text questions working exactly as before", async () => {
      vi.mocked(textToSqlApi.askQuestion).mockResolvedValue({
        natural_answer: "A free-text answer.",
        confidence: "medium",
        provenance: null,
      });
      const user = userEvent.setup();
      renderPage();

      await user.type(screen.getByLabelText("Question"), "Something I typed myself");
      await user.click(screen.getByRole("button", { name: "Ask" }));

      await waitFor(() => expect(screen.getByText("A free-text answer.")).toBeInTheDocument());
      expect(textToSqlApi.askQuestion).toHaveBeenCalledWith("Something I typed myself");
    });

    it("is disabled while a question is in flight", async () => {
      let resolveAnswer: (value: textToSqlApi.AskResponse) => void = () => {};
      vi.mocked(textToSqlApi.askQuestion).mockReturnValue(
        new Promise((resolve) => {
          resolveAnswer = resolve;
        }),
      );
      const user = userEvent.setup();
      renderPage();

      await user.type(screen.getByLabelText("Question"), "A slow question");
      await user.click(screen.getByRole("button", { name: "Ask" }));

      expect(screen.getByLabelText("Suggested questions")).toBeDisabled();

      resolveAnswer({ natural_answer: "Done.", confidence: "high", provenance: null });
      await waitFor(() => expect(screen.getByLabelText("Suggested questions")).toBeEnabled());
    });
  });

  describe("streaming ask", () => {
    const REQUEST_ID = "2f1c0c2e-7b1a-4e0a-9c3d-1a2b3c4d5e6f";

    function progress(
      stage: textToSqlApi.TextToSqlStage,
      status: textToSqlApi.TextToSqlStageStatus,
      message: string | null,
      percent: number | null = null,
    ): textToSqlApi.TextToSqlProgress {
      return {
        v: 1,
        kind: "text_to_sql",
        subject_id: REQUEST_ID,
        seq: 1,
        stage,
        status,
        percent,
        current: null,
        total: null,
        unit: null,
        message,
        updated_at: "2026-10-20T00:00:00Z",
      };
    }

    async function ask(user: ReturnType<typeof userEvent.setup>, question = "How many students do I teach?") {
      await user.type(screen.getByLabelText("Question"), question);
      await user.click(screen.getByRole("button", { name: "Ask" }));
    }

    it("shows the stage stepper, streamed answer, and percent", async () => {
      let release: (value: textToSqlApi.TextToSqlStreamResult) => void = () => {};
      vi.mocked(textToSqlApi.askQuestionStream).mockImplementation((_question, handlers) => {
        handlers?.onProgress?.(progress("checking", "running", "Checking your question"));
        handlers?.onProgress?.(progress("understanding", "running", "Understanding your question", 40));
        handlers?.onAnswerDelta?.("You teach ");
        handlers?.onAnswerDelta?.("10.");
        return new Promise((resolve) => {
          release = resolve;
        });
      });
      const user = userEvent.setup();
      renderPage();
      await ask(user);

      expect(screen.getByRole("list", { name: "Answer progress" })).toBeInTheDocument();
      expect(screen.getByText("Checking").closest("li")).toHaveTextContent("In progress");
      expect(screen.getByText("Checking").closest("li")).toHaveAttribute("aria-current", "step");
      const current = screen.getByText("Understanding").closest("li");
      expect(current).toHaveTextContent("In progress");
      expect(current).toHaveAttribute("aria-current", "step");
      expect(screen.getByText("Understanding your question")).toBeInTheDocument();
      expect(screen.queryByText("40%")).not.toBeInTheDocument();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
      expect(screen.getByText("You teach 10.")).toBeInTheDocument();

      release({
        kind: "final",
        requestId: REQUEST_ID,
        answer: { natural_answer: "You teach 10.", confidence: "high", provenance: null },
      });
      await waitFor(() => expect(screen.getByText("High confidence")).toBeInTheDocument());
      expect(textToSqlApi.askQuestion).not.toHaveBeenCalled();
    });

    it("renders the result table", async () => {
      vi.mocked(textToSqlApi.askQuestionStream).mockImplementation(async (_question, handlers) => {
        handlers?.onProgress?.(progress("running", "running", "Looking up the data"));
        handlers?.onTable?.({
          columns: ["Full name", "Mastery percent"],
          rows: [["Aisha Rahman", 82]],
        });
        return {
          kind: "final",
          requestId: REQUEST_ID,
          answer: { natural_answer: "Aisha Rahman is at 82%.", confidence: "high", provenance: null },
        };
      });
      const user = userEvent.setup();
      renderPage();
      await ask(user, "Show me the struggling students.");

      expect(await screen.findByRole("table", { name: "Query results" })).toBeInTheDocument();
      expect(screen.getByRole("columnheader", { name: "Full name" })).toBeInTheDocument();
      expect(screen.getByRole("cell", { name: "Aisha Rahman" })).toBeInTheDocument();
      expect(screen.getByRole("cell", { name: "82" })).toBeInTheDocument();
      expect(screen.getByText("Aisha Rahman is at 82%.")).toBeInTheDocument();
    });

    it("shows a refusal without a retry alert", async () => {
      vi.mocked(textToSqlApi.askQuestionStream).mockImplementation(async (_question, handlers) => {
        handlers?.onProgress?.(progress("checking", "failed", null));
        return {
          kind: "refusal",
          requestId: REQUEST_ID,
          message:
            "I can only help with questions about your school's students, classes, attendance, or curriculum data. Try asking something in that scope.",
        };
      });
      const user = userEvent.setup();
      renderPage();
      await ask(user, "What is the weather?");

      expect(
        await screen.findByText(/I can only help with questions about your school's students/),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("retries an error from a focused Retry button", async () => {
      vi.mocked(textToSqlApi.askQuestionStream)
        .mockResolvedValueOnce({
          kind: "error",
          requestId: REQUEST_ID,
          message: "That took too long to answer — please try again.",
        })
        .mockResolvedValueOnce({
          kind: "final",
          requestId: REQUEST_ID,
          answer: { natural_answer: "You teach 10.", confidence: "medium", provenance: null },
        });
      const user = userEvent.setup();
      renderPage();
      await ask(user);

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("That took too long to answer — please try again.");
      const retry = screen.getByRole("button", { name: "Retry" });
      expect(retry).toHaveAttribute("type", "button");
      retry.focus();
      expect(retry).toHaveFocus();
      await user.click(retry);
      await waitFor(() => expect(screen.getByText("You teach 10.")).toBeInTheDocument());
      expect(textToSqlApi.askQuestionStream).toHaveBeenCalledTimes(2);
      expect(textToSqlApi.askQuestionStream).toHaveBeenNthCalledWith(
        1,
        "How many students do I teach?",
        expect.any(Object),
      );
      expect(textToSqlApi.askQuestionStream).toHaveBeenNthCalledWith(
        2,
        "How many students do I teach?",
        expect.any(Object),
      );
    });

    it("shows the reconnecting badge and marks the stepper done after a terminal replay", async () => {
      let release: (value: textToSqlApi.TextToSqlStreamResult) => void = () => {};
      vi.mocked(textToSqlApi.askQuestionStream).mockImplementation((_question, handlers) => {
        handlers?.onAnswerDelta?.("Hello");
        handlers?.onAnswerReset?.();
        handlers?.onReconnecting?.({ attempt: 1, delayMs: 500 });
        return new Promise((resolve) => {
          release = resolve;
        });
      });
      const user = userEvent.setup();
      renderPage();
      await ask(user);

      const slot = screen.getByTestId("progress-reconnect");
      expect(slot).toHaveAttribute("data-active", "true");
      expect(await screen.findByText("Reconnecting…", {}, { timeout: 3_000 })).toBeInTheDocument();
      expect(screen.queryByText("Hello")).not.toBeInTheDocument();

      release({
        kind: "final",
        requestId: REQUEST_ID,
        answer: { natural_answer: "You teach 10.", confidence: "high", provenance: null },
      });
      await waitFor(() => expect(screen.getByText("You teach 10.")).toBeInTheDocument());
      expect(screen.getAllByText("Done")).toHaveLength(TEXT_TO_SQL_STAGE_ORDER.length);
      expect(screen.queryByText("Pending")).not.toBeInTheDocument();
    });

    it("shows the give-up message with Retry", async () => {
      vi.mocked(textToSqlApi.askQuestionStream).mockRejectedValue(
        new ProgressReconnectError(3, 60_000, new TypeError("connection reset")),
      );
      const user = userEvent.setup();
      renderPage();
      await ask(user);

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Lost the connection to progress updates. Try again.");
      expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    });

    it("falls back to the non-streaming ask when the stream endpoint is missing", async () => {
      let release: (value: textToSqlApi.AskResponse) => void = () => {};
      vi.mocked(textToSqlApi.askQuestionStream).mockRejectedValue(
        new ApiError("Not Found", 404, null),
      );
      vi.mocked(textToSqlApi.askQuestion).mockImplementation(
        () =>
          new Promise((resolve) => {
            release = resolve;
          }),
      );
      const user = userEvent.setup();
      renderPage();
      await ask(user);

      expect(await screen.findByText("Thinking…")).toBeInTheDocument();
      expect(screen.queryByRole("list", { name: "Answer progress" })).not.toBeInTheDocument();
      expect(screen.queryByText("Checking")).not.toBeInTheDocument();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

      release({ natural_answer: "Fallback answer.", confidence: "high", provenance: null });
      expect(await screen.findByText("Fallback answer.")).toBeInTheDocument();
      expect(screen.queryByRole("list", { name: "Answer progress" })).not.toBeInTheDocument();
      expect(textToSqlApi.askQuestion).toHaveBeenCalledWith("How many students do I teach?");
    });

    it("keeps the current stages visible while the audit delays the first answer", async () => {
      let release: (value: textToSqlApi.TextToSqlStreamResult) => void = () => {};
      vi.mocked(textToSqlApi.askQuestionStream).mockImplementation((_question, handlers) => {
        handlers?.onProgress?.(progress("checking", "running", "Checking your question"));
        handlers?.onProgress?.(progress("understanding", "running", "Understanding your question"));
        return new Promise((resolve) => {
          release = resolve;
        });
      });
      const user = userEvent.setup();
      renderPage();
      await ask(user);

      expect(screen.getByText("Checking").closest("li")).toHaveTextContent("In progress");
      expect(screen.getByText("Understanding").closest("li")).toHaveTextContent("In progress");
      expect(screen.getByText("Understanding your question")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByText("You teach 10.")).not.toBeInTheDocument();

      release({
        kind: "final",
        requestId: REQUEST_ID,
        answer: { natural_answer: "You teach 10.", confidence: "high", provenance: null },
      });
      expect(await screen.findByText("You teach 10.")).toBeInTheDocument();
    });

    it("cancels an in-flight ask", async () => {
      let signal: AbortSignal | undefined;
      vi.mocked(textToSqlApi.askQuestionStream).mockImplementation((_question, handlers) => {
        signal = handlers?.signal;
        return new Promise(() => undefined);
      });
      const user = userEvent.setup();
      renderPage();
      await ask(user);
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(signal?.aborted).toBe(true);
      expect(screen.getByText("Cancelled.")).toBeInTheDocument();
      expect(screen.getByLabelText("Question")).toBeEnabled();
    });

    it("aborts the stream when the page unmounts", async () => {
      let signal: AbortSignal | undefined;
      vi.mocked(textToSqlApi.askQuestionStream).mockImplementation((_question, handlers) => {
        signal = handlers?.signal;
        return new Promise(() => undefined);
      });
      const user = userEvent.setup();
      const view = renderPage();
      await ask(user);
      await waitFor(() => expect(signal).toBeInstanceOf(AbortSignal));
      view.unmount();
      expect(signal?.aborted).toBe(true);
    });

    it("marks every earlier stage done when the stream skips to running", async () => {
      vi.mocked(textToSqlApi.askQuestionStream).mockImplementation(async (_question, handlers) => {
        handlers?.onProgress?.(progress("running", "running", "Looking up the data"));
        return {
          kind: "final",
          requestId: REQUEST_ID,
          answer: { natural_answer: "Done.", confidence: "high", provenance: null },
        };
      });
      const user = userEvent.setup();
      renderPage();
      await ask(user, "Show me the struggling students.");

      const writing = await screen.findByText("Writing the query");
      expect(writing.closest("li")).toHaveTextContent("Done");
      expect(screen.getByText("Checking").closest("li")).toHaveTextContent("Done");
      expect(screen.getByText("Understanding").closest("li")).toHaveTextContent("Done");
      expect(screen.getByText("Validating").closest("li")).toHaveTextContent("Done");
      expect(screen.getByText("Running").closest("li")).toHaveTextContent("In progress");
      expect(screen.getByText("Running").closest("li")).toHaveAttribute("aria-current", "step");
      expect(screen.getByText("Summarising").closest("li")).toHaveTextContent("Pending");
    });
  });
});

describe("Assistant tab visibility for non-teacher roles", () => {
  function renderTeacherArea(initial: string) {
    return render(
      <MemoryRouter initialEntries={[initial]} future={ROUTER_FUTURE}>
        <Routes>
          <Route path="/login" element={<div>Login page</div>} />
          <Route path="/" element={<div>Student home</div>} />
          <Route
            path="/teacher"
            element={
              <RequireAuth>
                <RequireRole roles={ROLE_TEACHER}>
                  <TeacherShell />
                </RequireRole>
              </RequireAuth>
            }
          >
            <Route index element={<div>Classes page</div>} />
            <Route path="assistant" element={<AssistantPage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  }

  beforeEach(() => {
    vi.mocked(textToSqlApi.askQuestion).mockReset();
  });

  it("hides the Assistant nav entry and blocks the route for a student session", async () => {
    authState.user = { id: "s1", roles: ["student"], full_name: "A Student" };
    authState.loading = false;

    renderTeacherArea("/teacher/assistant");

    // Redirected away before TeacherShell (and its "Assistant" nav link) ever renders.
    await waitFor(() => {
      expect(screen.getByText("Student home")).toBeInTheDocument();
    });
    expect(screen.queryByText("Assistant")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
    expect(textToSqlApi.askQuestion).not.toHaveBeenCalled();
  });

  it("shows the Assistant nav entry and route for a teacher session", async () => {
    authState.user = { id: "t1", roles: ["teacher"], full_name: "A Teacher" };
    authState.loading = false;

    renderTeacherArea("/teacher/assistant");

    await waitFor(() => {
      expect(screen.getByLabelText("Question")).toBeInTheDocument();
    });
    expect(screen.getAllByText("Assistant").length).toBeGreaterThan(0);
  });
});
