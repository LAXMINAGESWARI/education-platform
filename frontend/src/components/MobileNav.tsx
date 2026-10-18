import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { HapticFeedbackToggle } from "./HapticFeedbackToggle";

export type MobileNavItem = {
  to: string;
  label: string;
  active: boolean;
};

type MobileNavProps = {
  brandTo: string;
  navLabel: string;
  items: MobileNavItem[];
  trailing?: ReactNode;
};

/** Phone header: brand, optional trailing action, and a drawer for the primary links.
 * The desktop rail stays in the shell; this bar is shown only under the mobile breakpoint. */
export function MobileNav({ brandTo, navLabel, items, trailing }: MobileNavProps) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const drawerId = useId();

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!open) return;
    drawerRef.current?.querySelector<HTMLElement>("a")?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  function close() {
    setOpen(false);
    buttonRef.current?.focus();
  }

  return (
    <>
      <div className="topbar">
        <div className="topbar__row">
          <button
            ref={buttonRef}
            type="button"
            className="topbar__menu"
            aria-expanded={open}
            aria-controls={drawerId}
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((value) => !value)}
          >
            <span className="topbar__menu-bars" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </button>
          <Link to={brandTo} className="topbar__brand">
            CurioEd
          </Link>
          {trailing ? <div className="topbar__trailing">{trailing}</div> : null}
        </div>
      </div>
      {open ? (
        <button type="button" className="nav-drawer__backdrop" aria-label="Close menu" onClick={close} />
      ) : null}
      <aside
        ref={drawerRef}
        id={drawerId}
        className={`nav-drawer${open ? " is-open" : ""}`}
        aria-label={navLabel}
        hidden={!open}
      >
        <nav className="nav-drawer__nav">
          {items.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={`rail__link${item.active ? " is-active" : ""}`}
              aria-current={item.active ? "page" : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <HapticFeedbackToggle />
      </aside>
    </>
  );
}
