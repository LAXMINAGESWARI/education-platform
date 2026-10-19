import type { ReactNode } from "react";

import { MarkdownContent } from "./MarkdownContent";

export function LessonSlideFrame({
  title,
  content,
  footer,
  ariaLabel = "Lesson",
  scrollKey,
  hideTitle = false,
}: {
  title: string;
  content: string;
  footer?: ReactNode;
  ariaLabel?: string;
  /** Remounts the scroller so a new slide opens at the top. */
  scrollKey?: string | number;
  hideTitle?: boolean;
}) {
  return (
    <article className="panel lesson-overview" aria-label={ariaLabel}>
      {!hideTitle && (
        <div className="lesson-overview__header">
          <h2>{title}</h2>
        </div>
      )}
      <div className="lesson-overview__scroll markdown" key={scrollKey} data-slide-index={scrollKey}>
        <MarkdownContent>{content}</MarkdownContent>
      </div>
      {footer ? <div className="lesson-overview__footer">{footer}</div> : null}
    </article>
  );
}
