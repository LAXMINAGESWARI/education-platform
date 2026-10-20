import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { SubjectStats } from "../lib/subjectStats";
import { SubjectStatsTable } from "./SubjectStatsTable";

const rows: SubjectStats[] = [
  {
    id: "math-1",
    name: "Mathematics",
    code: "MATH",
    gradeName: "Grade 8",
    periodName: "2026-27",
    href: "/subjects/math-1",
    status: "in_progress",
    topicsDone: 0,
    topicsTotal: 1,
    progressPercent: 25,
    lessonsDone: 1,
    lessonsTotal: 1,
    quizzesPassed: 0,
    quizzesAvailable: 2,
    attemptCount: 1,
    bestScorePercent: 50,
  },
  {
    id: "art-1",
    name: "Art",
    code: "ART",
    gradeName: "Grade 8",
    periodName: "2026-27",
    href: null,
    status: "coming_soon",
    topicsDone: 0,
    topicsTotal: 0,
    progressPercent: 0,
    lessonsDone: 0,
    lessonsTotal: 0,
    quizzesPassed: 0,
    quizzesAvailable: 0,
    attemptCount: 0,
    bestScorePercent: null,
  },
];

describe("SubjectStatsTable", () => {
  it("lists per-subject stats with status, subject, and score pills", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SubjectStatsTable rows={rows} />
      </MemoryRouter>,
    );

    const table = screen.getByRole("table", { name: "Subject progress" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Subject",
      "Status",
      "Topics",
      "Progress",
      "Lessons",
      "Quizzes",
      "Best score",
    ]);

    const math = within(table).getByRole("row", { name: /Mathematics/ });
    expect(within(math).getByText("Mathematics")).toHaveClass("badge", "badge--info");
    expect(within(math).getByText("MATH")).toHaveClass("badge");
    expect(within(math).getByText("In progress")).toHaveClass("badge", "badge--warn");
    expect(math).toHaveTextContent("0 of 1 complete");
    expect(math).toHaveTextContent("1 of 1 done");
    expect(math).toHaveTextContent("0 of 2 passed");
    expect(math).toHaveTextContent("1 attempt");
    expect(within(math).getByText("50%", { selector: ".badge", exact: false })).toHaveClass(
      "badge",
      "badge--warn",
    );
    expect(within(math).getByRole("progressbar", { name: "Mathematics average topic progress" })).toHaveAttribute(
      "aria-valuenow",
      "25",
    );
    expect(within(math).getByRole("link", { name: "Mathematics" })).toHaveAttribute(
      "href",
      "/subjects/math-1",
    );
    expect(within(math).getByRole("link", { name: "Open Mathematics" })).toHaveAttribute(
      "href",
      "/subjects/math-1",
    );

    const art = within(table).getByRole("row", { name: /Art/ });
    expect(within(art).getByText("Art")).toHaveClass("badge", "badge--locked");
    expect(within(art).getByText("Coming soon")).toHaveClass("badge", "badge--locked");
    expect(within(art).getByText("Not attempted")).toHaveClass("badge", "badge--info");
    expect(within(art).queryByRole("link")).not.toBeInTheDocument();

    await user.tab();
    expect(within(math).getByRole("link", { name: "Mathematics" })).toHaveFocus();
  });
});
