import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/types";
import { FeedbackSubjectPickerPage } from "./FeedbackSubjectPickerPage";
import { HomePage, clearLearningDirectoryCache } from "./HomePage";

vi.mock("../api/feedback", () => ({
  getFeedbackOverview: vi.fn(),
  getFeedbackHighlights: vi.fn(),
}));

vi.mock("../api/materials", () => ({
  fetchLearningDirectory: vi.fn(),
}));

import { getFeedbackHighlights, getFeedbackOverview } from "../api/feedback";
import { fetchLearningDirectory } from "../api/materials";

describe("Feedback and Subjects empty states", () => {
  beforeEach(() => {
    clearLearningDirectoryCache();
    vi.mocked(fetchLearningDirectory).mockReset();
    vi.mocked(getFeedbackOverview).mockReset();
    vi.mocked(getFeedbackHighlights).mockReset();
  });

  it("renders the Subjects page with empty enrollment data", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue({ subjects: [] });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Your subjects" })).toBeInTheDocument();
    expect(
      await screen.findByText("No enrolled subjects with published curriculum yet."),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Coming soon" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Science" })).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Subject progress" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders the Feedback page with empty enrollment data", async () => {
    vi.mocked(getFeedbackOverview).mockResolvedValue({ score_percent: null, subjects: [] });
    vi.mocked(getFeedbackHighlights).mockResolvedValue({ due_reviews: [], trajectories: [] });
    vi.mocked(fetchLearningDirectory).mockResolvedValue({ subjects: [] });

    render(
      <MemoryRouter>
        <FeedbackSubjectPickerPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Feedback" })).toBeInTheDocument();
    expect(await screen.findByText("No enrolled subjects yet.")).toBeInTheDocument();
    const summary = screen.getByRole("region", { name: "Across all subjects" });
    expect(summary).toHaveTextContent("Not attempted");
    expect(summary).toHaveTextContent("Concepts mastered out of those you've tried");
    expect(summary).not.toHaveTextContent("Concepts you have already tried");
    expect(screen.getByRole("region", { name: "Across all subjects" })).not.toHaveTextContent("0%");
    expect(screen.queryByRole("table", { name: "Feedback by subject" })).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Due for review" })).not.toBeInTheDocument();
  });

  it("shows a loading status and then an error alert on the Subjects page", async () => {
    vi.mocked(fetchLearningDirectory).mockReturnValue(new Promise(() => undefined));
    const { unmount } = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Loading subjects…");
    unmount();

    clearLearningDirectoryCache();
    vi.mocked(fetchLearningDirectory).mockRejectedValue(new ApiError("Could not load subjects.", 500, null));
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load subjects.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Science" })).not.toBeInTheDocument();
  });
});
