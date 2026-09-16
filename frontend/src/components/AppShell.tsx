import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";

import { resetDemoProgress } from "../api/demo";
import { ApiError } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ConfirmDialog } from "./ConfirmDialog";
import { RouteMotion } from "./RouteMotion";

const IS_DEV = import.meta.env.DEV;
const RAIL_COLLAPSED_KEY = "ep.studentRailCollapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(RAIL_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function initialsFromName(name: string | undefined): string {
  const parts = (name ?? "A").trim().split(/\s+/).filter(Boolean);
  const letters = (parts[0]?.[0] ?? "A") + (parts[1]?.[0] ?? "");
  return letters.toUpperCase();
}

export function AppShell({ children }: { children?: React.ReactNode }) {
  const page = children ?? <Outlet />;
  const { user, enrolled, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [railCollapsed, setRailCollapsed] = useState(false);

  useEffect(() => {
    setRailCollapsed(readCollapsed());
  }, []);

  function toggleRail() {
    setRailCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(RAIL_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  const onHome = !location.pathname.startsWith("/feedback");
  const onFeedback = location.pathname.startsWith("/feedback");

  useEffect(() => {
    if (!menuOpen) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  async function onResetDemo() {
    setResetBusy(true);
    setResetError(null);
    try {
      await resetDemoProgress();
      setResetOpen(false);
      navigate("/", { replace: true });
      window.location.assign("/");
    } catch (err) {
      setResetError(err instanceof ApiError ? err.message : "Could not reset demo.");
      setResetBusy(false);
    }
  }

  return (
    <div className="app-frame">
      <div ref={menuRef} className="profile-dock">
        <button
          type="button"
          className="profile-dock__avatar"
          aria-label="Account menu"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          title={user?.full_name ?? "Account"}
          onClick={() => setMenuOpen((open) => !open)}
        >
          {initialsFromName(user?.full_name)}
        </button>
        {menuOpen && (
          <div className="profile-dock__menu" role="menu" aria-label="Account">
            <p className="profile-dock__name">{user?.full_name ?? "Student"}</p>
            {IS_DEV && enrolled && (
              <button
                type="button"
                className="profile-dock__item"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  setResetError(null);
                  setResetOpen(true);
                }}
              >
                Reset demo
              </button>
            )}
            <button
              type="button"
              className="profile-dock__item"
              role="menuitem"
              onClick={() => void signOut()}
            >
              Sign out
            </button>
          </div>
        )}
      </div>

      <div className={`app ${railCollapsed ? "is-rail-collapsed" : ""}`}>
        <aside
          className={`rail ${railCollapsed ? "is-collapsed" : ""}`}
          aria-label="Primary"
          data-elevated="true"
        >
          <div className="rail__top">
            {!railCollapsed && (
              <Link to="/" className="rail__brand">
                Education Platform
              </Link>
            )}
            <button
              type="button"
              className="rail__toggle"
              aria-expanded={!railCollapsed}
              aria-controls="student-rail-nav"
              title={railCollapsed ? "Expand navigation" : "Collapse navigation"}
              aria-label={railCollapsed ? "Expand navigation" : "Collapse navigation"}
              onClick={toggleRail}
            >
              {railCollapsed ? "»" : "«"}
            </button>
          </div>
          <nav id="student-rail-nav" className="rail__nav" aria-label="Student">
            <Link to="/" className={`rail__link ${onHome ? "is-active" : ""}`} title="Home">
              <span className="rail__link-short" aria-hidden="true">
                H
              </span>
              <span className="rail__link-label">Home</span>
            </Link>
            <Link
              to="/feedback"
              className={`rail__link ${onFeedback ? "is-active" : ""}`}
              title="Feedback"
            >
              <span className="rail__link-short" aria-hidden="true">
                F
              </span>
              <span className="rail__link-label">Feedback</span>
            </Link>
          </nav>
        </aside>

        <div className="app__content">
          <div className="topbar">
            <div className="topbar__row">
              <Link to="/" className="topbar__brand">
                Education Platform
              </Link>
            </div>
            <nav className="rail__nav rail__nav--horizontal" aria-label="Mobile student">
              <Link to="/" className={`rail__link ${onHome ? "is-active" : ""}`}>
                Home
              </Link>
              <Link to="/feedback" className={`rail__link ${onFeedback ? "is-active" : ""}`}>
                Feedback
              </Link>
            </nav>
          </div>

          <main className="main">
            <RouteMotion>{page}</RouteMotion>
          </main>
        </div>
      </div>

      <ConfirmDialog
        open={resetOpen}
        title="Demo reset"
        body={
          resetError ??
          "All quiz progress and attempt history will be cleared. Subjects start fresh."
        }
        onDismiss={() => {
          if (!resetBusy) setResetOpen(false);
        }}
        actions={[
          { label: "Cancel", variant: "soft" },
          {
            label: resetBusy ? "Resetting…" : "Reset demo",
            keepOpen: true,
            onClick: () => {
              void onResetDemo();
            },
          },
        ]}
      />
    </div>
  );
}
