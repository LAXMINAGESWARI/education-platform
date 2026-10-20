import type { BloomLevel, GenerationQaItem } from "../api/types";
import { presentQuizText } from "../lib/quizText";
import { MarkdownContent } from "./MarkdownContent";
import "./TopicGenerationQa.css";

type QuizItemCardsProps = {
  items: GenerationQaItem[];
  canSelect?: boolean;
  busy?: boolean;
  selected?: ReadonlySet<string>;
  allSelected?: boolean;
  onToggle?: (questionId: string) => void;
  onToggleAll?: () => void;
};

function bloomLabel(bloom: BloomLevel | null | undefined): string {
  if (bloom == null) return "Unlabeled";
  switch (bloom) {
    case "remember":
      return "Remember";
    case "understand":
      return "Understand";
    case "apply":
      return "Apply";
    case "analyze":
      return "Analyze";
    default: {
      const _never: never = bloom;
      return _never;
    }
  }
}

function difficultyLabel(value: string | null | undefined): string | null {
  switch (value) {
    case "easy":
      return "Easy";
    case "medium":
      return "Medium";
    case "hard":
      return "Hard";
    case null:
    case undefined:
    case "":
      return null;
    default:
      return value;
  }
}

function optionEntries(options: Record<string, string>): [string, string][] {
  return Object.entries(options).sort(([left], [right]) => left.localeCompare(right));
}

function checkBadge(item: GenerationQaItem): string | null {
  const verdict = item.check_verdict;
  if (verdict == null || verdict === "" || verdict === "passed") return null;
  const attempts = item.check_attempts ?? 1;
  if (verdict === "fixed_on_retry") return `Fixed on retry · ${attempts} attempts`;
  if (verdict === "dropped") return `Dropped · ${attempts} attempts`;
  return verdict;
}

function reviewBadge(item: GenerationQaItem): string | null {
  if (item.judge_needs_review === true) return "Needs review";
  return null;
}

function disputedBadge(item: GenerationQaItem): string | null {
  if (item.key_disputed !== true || item.blind_answer == null || item.blind_answer === "") {
    return null;
  }
  return `Key disputed - solver chose ${item.blind_answer}`;
}

export function QuizItemCards({
  items,
  canSelect = false,
  busy = false,
  selected,
  allSelected = false,
  onToggle,
  onToggleAll,
}: QuizItemCardsProps) {
  return (
    <div className="quiz-item-cards">
      {canSelect ? (
        <label className="qa-review__select">
          <input
            type="checkbox"
            checked={allSelected}
            disabled={busy}
            onChange={onToggleAll}
            aria-label="Select all items"
          />
          Select
        </label>
      ) : null}
      <ol className="quiz-item-cards__list">
        {items.map((item) => {
          const difficulty = difficultyLabel(item.difficulty);
          const misconceptions = item.misconception_labels ?? [];
          const disputed = disputedBadge(item);
          const checked = checkBadge(item);
          const review = reviewBadge(item);
          return (
            <li key={item.question_id} className="quiz-item-card">
              <div className="quiz-item-card__meta">
                {canSelect ? (
                  <input
                    type="checkbox"
                    checked={selected?.has(item.question_id) ?? false}
                    disabled={busy}
                    onChange={() => onToggle?.(item.question_id)}
                    aria-label={`Select item ${item.sequence}`}
                  />
                ) : null}
                <span className="quiz-item-card__number">Q{item.sequence}</span>
                {item.subtopic_title ? (
                  <span className="quiz-item-card__chip">{item.subtopic_title}</span>
                ) : null}
                <span className="quiz-item-card__chip">{bloomLabel(item.bloom)}</span>
                {difficulty ? <span className="quiz-item-card__chip">{difficulty}</span> : null}
                {misconceptions.map((label) => (
                  <span key={label} className="quiz-item-card__chip">
                    {label}
                  </span>
                ))}
              </div>
              <div className="quiz-item-card__prompt">
                <MarkdownContent>{presentQuizText(item.prompt)}</MarkdownContent>
              </div>
              {review ? (
                <p className="qa-review__disputed">
                  <span className="badge badge--warn">{review}</span>
                </p>
              ) : null}
              {disputed ? (
                <p className="qa-review__disputed">
                  <span className="badge badge--warn">{disputed}</span>
                </p>
              ) : null}
              {checked ? (
                <p className="qa-review__disputed">
                  <span className="badge badge--warn">{checked}</span>
                </p>
              ) : null}
              <ul className="draft-options">
                {optionEntries(item.options).map(([label, text]) => {
                  const correct = label === item.correct_label;
                  return (
                    <li
                      key={label}
                      className={correct ? "draft-option is-correct" : "draft-option"}
                    >
                      <span className="draft-option__label">{label}</span>
                      <span>
                        <MarkdownContent inline>{presentQuizText(text)}</MarkdownContent>
                      </span>
                      {correct ? <span className="quiz-item-card__correct">Correct</span> : <span />}
                    </li>
                  );
                })}
              </ul>
              <p className="quiz-item-card__explanation">
                <MarkdownContent inline>{presentQuizText(item.correct_rationale)}</MarkdownContent>
              </p>
              {optionEntries(item.distractor_rationales).map(([label, text]) => (
                <p key={label} className="qa-review__rationale muted">
                  {label}: <MarkdownContent inline>{presentQuizText(text)}</MarkdownContent>
                </p>
              ))}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
