import type { ReactNode } from "react";

import type { LessonSlide } from "../api/types";
import { LessonSlideFrame } from "./LessonSlideFrame";
import { PushButton } from "./PushButton";
import { SlideProgress } from "./SlideProgress";

export function LessonSlideStage({
  slides,
  index,
  onIndexChange,
  ariaLabel,
  leading,
  endAction,
  hint,
  after,
  complete = false,
}: {
  slides: LessonSlide[];
  index: number;
  onIndexChange: (index: number) => void;
  ariaLabel: string;
  leading?: ReactNode;
  /** Replaces Next on the last slide. The student quiz link uses this. */
  endAction?: ReactNode;
  hint?: ReactNode;
  after?: ReactNode;
  complete?: boolean;
}) {
  const slide = slides[index];
  if (!slide) return null;
  const atEnd = index >= slides.length - 1;
  const atStart = index <= 0;

  return (
    <div className="lesson-slide-stage">
      <div className="lesson-stage__bar">
        {leading}
        <SlideProgress
          heading
          sectionTitle={slide.sectionTitle ?? slide.title}
          index={index}
          total={slides.length}
          complete={complete}
        />
        {slides.length > 1 ? (
          <label className="lesson-slide-stage__jump">
            <span className="lesson-slide-stage__jump-label">Slide</span>
            <select
              aria-label="Jump to slide"
              value={String(index)}
              onChange={(event) => onIndexChange(Number(event.target.value))}
            >
              {slides.map((item, itemIndex) => (
                <option key={`${item.number}-${itemIndex}`} value={String(itemIndex)}>
                  {itemIndex + 1}. {item.sectionTitle ?? item.title}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <section className="lesson-slides" aria-label={ariaLabel}>
        <LessonSlideFrame
          title={slide.title}
          content={slide.content}
          ariaLabel={ariaLabel}
          scrollKey={index}
          hideTitle
          footer={
            <>
              <div className="slide-nav__controls">
                <PushButton
                  variant="outline"
                  size="sm"
                  disabled={atStart}
                  onClick={() => onIndexChange(Math.max(0, index - 1))}
                >
                  Previous
                </PushButton>
                {atEnd && endAction != null ? (
                  endAction
                ) : (
                  <PushButton
                    size="sm"
                    disabled={atEnd}
                    onClick={() => onIndexChange(Math.min(slides.length - 1, index + 1))}
                  >
                    Next slide
                  </PushButton>
                )}
              </div>
              {hint}
            </>
          }
        />
        {after}
      </section>
    </div>
  );
}
