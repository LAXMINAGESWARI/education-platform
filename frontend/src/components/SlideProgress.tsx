export function SlideProgress({
  sectionTitle,
  index,
  total,
  complete = false,
}: {
  sectionTitle?: string;
  index: number;
  total: number;
  complete?: boolean;
}) {
  return (
    <div className="slide-nav__progress">
      {sectionTitle ? <p className="slide-nav__section">{sectionTitle}</p> : null}
      <p className="slide-nav__status">
        Slide {index + 1} of {total}
        {complete ? " · complete" : ""}
      </p>
    </div>
  );
}
