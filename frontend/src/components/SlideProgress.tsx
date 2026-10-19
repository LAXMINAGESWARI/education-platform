export function SlideProgress({
  sectionTitle,
  index,
  total,
  complete = false,
  heading = false,
}: {
  sectionTitle?: string;
  index: number;
  total: number;
  complete?: boolean;
  heading?: boolean;
}) {
  const Title = heading ? "h2" : "p";
  return (
    <div className="slide-nav__progress">
      {sectionTitle ? <Title className="slide-nav__section">{sectionTitle}</Title> : null}
      <p className="slide-nav__status">
        Slide {index + 1} of {total}
        {complete ? " · complete" : ""}
      </p>
    </div>
  );
}
