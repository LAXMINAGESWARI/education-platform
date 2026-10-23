import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { FeedbackSubjectCard } from "../api/types";
import { DueReviewsTable, FeedbackEntriesTable, type DueReviewRow } from "./FeedbackEntriesTable";

const subjects: FeedbackSubjectCard[] = [
  {
    subject_id: "math-1",
    subject_name: "Mathematics",
    grade_name: "Grade 8",
    score_percent: "80",
    quizzes_taken: 4,
    trend: "improving",
    trend_delta: "20",
    strongest_topic: {
      topic_id: "mensuration",
      topic_name: "Mensuration",
      best_score_percent: "90",
    },
    weakest_topic: {
      topic_id: "algebra",
      topic_name: "Algebra",
      best_score_percent: "40",
    },
    concepts_mastered: 2,
    concepts_attempted: 4,
    focus: { topic_name: "Algebra", topic_id: "algebra", gap_percent: 8 },
    achievement: "proficient",
  },
  {
    subject_id: "sci-1",
    subject_name: "Science",
    grade_name: "Grade 8",
    score_percent: null,
    quizzes_taken: 0,
    trend: null,
    trend_delta: null,
    strongest_topic: null,
    weakest_topic: null,
    concepts_mastered: 0,
    concepts_attempted: 0,
    focus: null,
    achievement: null,
  },
];

const due: DueReviewRow[] = [
  {
    subtopicId: "st-1",
    subtopicName: "Properties of rectangles",
    topicName: "Mensuration",
    daysSinceLastAttempt: 12,
    dueIntervalDays: 7,
    subjectId: "math-1",
    subjectName: "Mathematics",
    href: "/subjects/math-1/feedback",
  },
];

describe("FeedbackEntriesTable", () => {
  it("lists feedback rows with subject, status, and score pills", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <FeedbackEntriesTable subjects={subjects} />
      </MemoryRouter>,
    );

    const table = screen.getByRole("table", { name: "Feedback by subject" });
    expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Subject",
      "Score",
      "Status",
      "Quizzes",
      "Trend",
      "Concepts",
      "Highlights",
    ]);

    const math = within(table).getByRole("row", { name: /Mathematics/ });
    expect(within(math).getByRole("link", { name: "Mathematics" })).toHaveClass("catalog-subject__link");
    expect(within(math).getByText("Mathematics")).toHaveClass("badge", "badge--info");
    expect(within(math).getByText("80%", { selector: ".badge", exact: false })).toHaveClass(
      "badge",
      "badge--ok",
    );
    expect(within(math).getByText("Proficient")).toHaveClass("badge", "badge--info");
    expect(math).toHaveTextContent("Solid understanding across all concepts!");
    expect(math).toHaveTextContent("4 topic quizzes");
    expect(within(math).getByText("Improving")).toHaveClass("badge", "badge--ok");
    expect(math).toHaveTextContent("Up 20 points since the previous attempt");
    expect(math).toHaveTextContent("2 of 4 mastered");
    expect(math).toHaveTextContent("Strongest topic: Mensuration");
    expect(within(math).getByText("90%", { selector: ".badge", exact: false })).toHaveClass(
      "badge",
      "badge--ok",
    );
    expect(math).toHaveTextContent("Weakest topic: Algebra");
    expect(within(math).getByText("40%", { selector: ".badge", exact: false })).toHaveClass(
      "badge",
      "badge--warn",
    );
    expect(math).toHaveTextContent("You're just 8% from your goal on Algebra.");
    expect(within(math).getByRole("link", { name: "View feedback" })).toHaveAttribute(
      "href",
      "/subjects/math-1/feedback",
    );

    const science = within(table).getByRole("row", { name: /Science/ });
    expect(science).toHaveTextContent("Not attempted");
    expect(science).toHaveTextContent("No topic quizzes yet");
    expect(science).not.toHaveTextContent("0%");
    expect(science).not.toHaveTextContent("declining");
    expect(science).not.toHaveTextContent(/· -/);
    expect(within(science).getAllByText("Not attempted").length).toBeGreaterThan(0);
    expect(within(science).getAllByText("Not attempted")[0]).toHaveClass("badge");

    await user.tab();
    expect(within(math).getByRole("link", { name: "Mathematics" })).toHaveFocus();
  });

  it("renders mastery, advanced, on-track, declining, and flat badges", () => {
    const variants: FeedbackSubjectCard[] = [
      {
        ...subjects[0],
        subject_id: "mastery-1",
        subject_name: "Mastery Subject",
        achievement: "mastery",
        trend: "declining",
        trend_delta: "5",
      },
      {
        ...subjects[0],
        subject_id: "advanced-1",
        subject_name: "Advanced Subject",
        achievement: "advanced",
        trend: "flat",
        trend_delta: "0",
      },
      {
        ...subjects[0],
        subject_id: "track-1",
        subject_name: "On Track Subject",
        achievement: "on_track",
        trend: "improving",
        trend_delta: "3",
      },
    ];

    render(
      <MemoryRouter>
        <FeedbackEntriesTable subjects={variants} />
      </MemoryRouter>,
    );

    const mastery = screen.getByRole("row", { name: /Mastery Subject/ });
    expect(within(mastery).getByText("Mastery")).toHaveClass("badge", "badge--ok");
    expect(within(mastery).getByText("Declining")).toHaveClass("badge", "badge--danger");

    const advanced = screen.getByRole("row", { name: /Advanced Subject/ });
    expect(within(advanced).getByText("Advanced")).toHaveClass("badge", "badge--ok");
    expect(within(advanced).getByText("No change")).toHaveClass("badge", "badge--locked");

    const onTrack = screen.getByRole("row", { name: /On Track Subject/ });
    expect(within(onTrack).getByText("On track")).toHaveClass("badge", "badge--info");
    expect(within(onTrack).getByText("Improving")).toHaveClass("badge", "badge--ok");
  });
});

describe("DueReviewsTable", () => {
  it("shows the topic, interval, and subject for each due review", () => {
    render(
      <MemoryRouter>
        <DueReviewsTable rows={due} />
      </MemoryRouter>,
    );

    const table = screen.getByRole("table", { name: "Due for review" });
    const row = within(table).getByRole("row", { name: /Properties of rectangles/ });
    expect(row).toHaveTextContent("Mensuration");
    expect(within(row).getByText("Mathematics")).toHaveClass("badge", "badge--info");
    expect(within(row).getByText("Due")).toHaveClass("badge", "badge--warn");
    expect(row).toHaveTextContent("Last attempted 12 days ago");
    expect(row).toHaveTextContent("Every 7 days");
    expect(within(row).getByRole("link", { name: "Review" })).toHaveAttribute(
      "href",
      "/subjects/math-1/feedback",
    );
  });
});
