import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as chatsApi from "../../api/chats";
import { PolicyChatPage } from "./PolicyChatPage";

vi.mock("../../api/chats", () => ({
  listChats: vi.fn(),
  createChat: vi.fn(),
  getChat: vi.fn(),
  deleteChat: vi.fn(),
  postChatMessageStream: vi.fn(),
}));

const sampleContext = { used_tokens: 120, limit_tokens: 20_000, used_percent: 1 };

describe("PolicyChatPage", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(chatsApi.listChats).mockResolvedValue([
      {
        id: "c1",
        title: "Attendance",
        updated_at: "2026-08-15T00:00:00Z",
        context: sampleContext,
      },
    ]);
    vi.mocked(chatsApi.getChat).mockResolvedValue({
      id: "c1",
      title: "Attendance",
      updated_at: "2026-08-15T00:00:00Z",
      context: sampleContext,
      messages: [
        {
          id: "m1",
          role: "assistant",
          content: "Ask a policy question.",
          created_at: "2026-08-15T00:00:00Z",
        },
      ],
    });
    vi.mocked(chatsApi.createChat).mockResolvedValue({
      id: "c2",
      title: "New chat",
      updated_at: "2026-08-15T00:00:00Z",
      context: sampleContext,
    });
    vi.mocked(chatsApi.postChatMessageStream).mockImplementation(async (_id, _content, handlers) => {
      handlers?.onProgress?.({
        v: 1,
        kind: "policy_chat",
        subject_id: "req-1",
        seq: 1,
        stage: "checking",
        status: "running",
        percent: 25,
        current: null,
        total: null,
        unit: null,
        message: "Checking your question",
        updated_at: "2026-08-15T00:01:00Z",
      });
      return {
        kind: "final",
        requestId: "req-1",
        message: {
          user_message: {
            id: "m2",
            role: "user",
            content: "What is attendance policy?",
            created_at: "2026-08-15T00:01:00Z",
          },
          assistant_message: {
            id: "m3",
            role: "assistant",
            content: "## Attendance\n\nNotify the office after **three** absences.",
            citations: [{ id: "1", label: "Handbook", excerpt: "three absences" }],
            created_at: "2026-08-15T00:01:01Z",
          },
          context: { used_tokens: 400, limit_tokens: 20_000, used_percent: 2 },
        },
      };
    });
  });

  it("loads conversations, shows context percent, and sends a message", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <PolicyChatPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Ask a policy question.")).toBeInTheDocument();
    });
    expect(screen.queryByText(/Administrator · grounded policy lookup/)).not.toBeInTheDocument();
    expect(document.querySelector(".page-head")).toBeNull();
    expect(screen.queryByText(/Answers are grounded in indexed/)).not.toBeInTheDocument();
    expect(
      screen.getByLabelText(/Context window 120 of 20,000 tokens, 1 percent/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText("120 / 20,000 tokens · 1%", { selector: ".policy-chat__context-tip" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Attendance", { selector: ".policy-chat__conv-title" })).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("Message"),
      "What is attendance policy?",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Attendance" })).toBeInTheDocument();
    });
    expect(screen.getByText("three", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText(/Notify the office after/)).toBeInTheDocument();
    expect(screen.getByText("Sources (1)")).toBeInTheDocument();
    expect(screen.queryByLabelText("Filter")).not.toBeVisible();
    expect(screen.queryByText("three absences")).not.toBeVisible();

    await user.click(screen.getByText("Sources (1)"));

    expect(screen.getByLabelText("Filter")).toHaveDisplayValue("All types");
    expect(screen.getByRole("option", { name: "Handbook" })).toBeInTheDocument();
    expect(screen.getByText("three absences")).toBeVisible();
    expect(screen.queryByText("Running safety checks and retrieval…")).not.toBeInTheDocument();
    expect(
      screen.getByLabelText(/Context window 400 of 20,000 tokens, 2 percent/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText("400 / 20,000 tokens · 2%", { selector: ".policy-chat__context-tip" }),
    ).toBeInTheDocument();
  });

  it("shows the live stage and draft, then the saved answer", async () => {
    const user = userEvent.setup();
    let release: (value: Awaited<ReturnType<typeof chatsApi.postChatMessageStream>>) => void =
      () => {};
    const gate = new Promise<Awaited<ReturnType<typeof chatsApi.postChatMessageStream>>>(
      (resolve) => {
        release = resolve;
      },
    );
    vi.mocked(chatsApi.postChatMessageStream).mockImplementation(async (_id, _content, handlers) => {
      handlers?.onProgress?.({
        v: 1,
        kind: "policy_chat",
        subject_id: "req-1",
        seq: 1,
        stage: "checking",
        status: "running",
        percent: 25,
        current: null,
        total: null,
        unit: null,
        message: "Checking your question",
        updated_at: "2026-08-15T00:01:00Z",
      });
      handlers?.onProgress?.({
        v: 1,
        kind: "policy_chat",
        subject_id: "req-1",
        seq: 2,
        stage: "searching",
        status: "running",
        percent: 60,
        current: null,
        total: null,
        unit: null,
        message: "Searching policy documents",
        updated_at: "2026-08-15T00:01:01Z",
      });
      handlers?.onAnswerDelta?.("STREAMED");
      return gate;
    });

    render(
      <MemoryRouter>
        <PolicyChatPage />
      </MemoryRouter>,
    );
    await screen.findByText("Ask a policy question.");
    await user.type(screen.getByLabelText("Message"), "What is attendance policy?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Searching policy documents")).toBeInTheDocument();
    expect(screen.getByText("STREAMED")).toBeInTheDocument();
    expect(screen.queryByText("Running safety checks and retrieval…")).not.toBeInTheDocument();

    release({
      kind: "final",
      requestId: "req-1",
      message: {
        user_message: {
          id: "m2",
          role: "user",
          content: "What is attendance policy?",
          created_at: "2026-08-15T00:01:00Z",
        },
        assistant_message: {
          id: "m3",
          role: "assistant",
          content: "## Attendance\n\nNotify the office after **three** absences.",
          citations: [{ id: "1", label: "Handbook", excerpt: "three absences" }],
          created_at: "2026-08-15T00:01:01Z",
        },
        context: { used_tokens: 400, limit_tokens: 20_000, used_percent: 2 },
      },
    });

    await waitFor(() => {
      expect(screen.queryByText("STREAMED")).not.toBeInTheDocument();
    });
    expect(screen.getByText("three", { selector: "strong" })).toBeInTheDocument();
    expect(screen.queryByText("Searching policy documents")).not.toBeInTheDocument();
  });

  it("starts with chats collapsed into the header on a phone-width viewport", async () => {
    const original = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("max-width"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia;

    try {
      render(
        <MemoryRouter>
          <PolicyChatPage />
        </MemoryRouter>,
      );

      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Expand chats" })).toHaveAttribute(
          "aria-expanded",
          "false",
        );
        expect(
          screen.getByText("Attendance", { selector: ".policy-chat__conv-title" }),
        ).not.toBeVisible();
      });
      expect(document.getElementById("policy-chat-conversations")).toHaveAttribute("hidden");
      expect(screen.getByRole("button", { name: "New" })).toBeEnabled();
    } finally {
      window.matchMedia = original;
    }
  });

  it("collapses and expands the conversation sidebar", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <PolicyChatPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Attendance", { selector: ".policy-chat__conv-title" })).toBeVisible();
    });

    const collapse = screen.getByRole("button", { name: "Collapse chats" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    await user.click(collapse);

    expect(screen.getByRole("button", { name: "Expand chats" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(document.querySelector(".policy-chat__layout.is-sidebar-collapsed")).not.toBeNull();
    expect(
      screen.getByText("Attendance", { selector: ".policy-chat__conv-title" }),
    ).not.toBeVisible();

    await user.click(screen.getByRole("button", { name: "Expand chats" }));
    expect(screen.getByRole("button", { name: "Collapse chats" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByText("Attendance", { selector: ".policy-chat__conv-title" })).toBeVisible();
  });
});
