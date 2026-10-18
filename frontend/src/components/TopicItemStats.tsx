import { useEffect, useState } from "react";

import { fetchTopicItemStats } from "../api/generation";
import type { ItemStatFlag, TopicItemStat, TopicItemStats as TopicItemStatsReport } from "../api/types";

type TopicItemStatsProps = {
  topicId: string;
};

function flagLabel(flag: ItemStatFlag): string {
  switch (flag) {
    case "too_easy":
      return "Too easy";
    case "too_hard":
      return "Too hard";
    case "low_discrimination":
      return "Low discrimination";
    case "negative_discrimination":
      return "Negative discrimination";
    case "dead_distractor":
      return "Dead distractor";
    case "distractor_beats_key_top_group":
      return "Distractor beats key";
    default: {
      const _never: never = flag;
      return _never;
    }
  }
}

function formatRate(rate: number | null): string {
  if (rate == null) return "—";
  return `${(rate * 100).toFixed(1)}%`;
}

function formatDiscrimination(value: number | null): string {
  if (value == null) return "—";
  return value.toFixed(2);
}

function optionSummary(item: TopicItemStat): string {
  return item.options
    .map((option) => {
      const rate = formatRate(option.pick_rate);
      return option.is_key ? `${option.label} (key) ${rate}` : `${option.label} ${rate}`;
    })
    .join(" · ");
}

export function TopicItemStats({ topicId }: TopicItemStatsProps) {
  const [stats, setStats] = useState<TopicItemStatsReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setStats(null);
    setError(null);
    void (async () => {
      try {
        const data = await fetchTopicItemStats(topicId);
        if (!cancelled) setStats(data);
      } catch {
        if (!cancelled) setError("Could not load item statistics.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [topicId]);

  let body;
  if (error) {
    body = (
      <p className="form__error" role="alert">
        {error}
      </p>
    );
  } else if (!stats) {
    body = (
      <p className="muted" role="status">
        Loading item statistics…
      </p>
    );
  } else {
    const ready = stats.items
      .filter((item) => item.n >= stats.minimum_n)
      .sort((left, right) => left.sequence - right.sequence);
    body =
      ready.length === 0 ? (
        <p className="muted" role="status">
          Not enough responses to flag items yet. Flags start once a question has at least{" "}
          {stats.minimum_n} responses.
        </p>
      ) : (
        <div className="table-scroll">
          <table className="data-table data-table--cards">
            <thead>
              <tr>
                <th scope="col">Question</th>
                <th scope="col">Responses</th>
                <th scope="col">Correct</th>
                <th scope="col">Discrimination</th>
                <th scope="col">Flags</th>
              </tr>
            </thead>
            <tbody>
              {ready.map((item) => (
                <tr key={item.question_version_id}>
                  <td data-label="Question">
                    <p className="topic-item-stats__prompt">
                      <strong>Q{item.sequence}.</strong> {item.prompt}
                    </p>
                    <p className="muted topic-item-stats__options">{optionSummary(item)}</p>
                  </td>
                  <td data-label="Responses">{item.n}</td>
                  <td data-label="Correct">{formatRate(item.p_correct)}</td>
                  <td data-label="Discrimination">{formatDiscrimination(item.discrimination)}</td>
                  <td data-label="Flags">
                    {item.flags.length === 0 ? (
                      <span className="muted">None</span>
                    ) : (
                      <span className="topic-item-stats__flags">
                        {item.flags.map((flag) => (
                          <span key={flag} className="badge badge--warn">
                            {flagLabel(flag)}
                          </span>
                        ))}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }

  return (
    <section className="topic-item-stats" aria-label="Item statistics">
      <h2 className="topic-item-stats__title">Item statistics</h2>
      {body}
    </section>
  );
}
