import { Link, Outlet, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";

import { useAuth } from "../auth/AuthContext";
import { MobileNav } from "./MobileNav";
import { PwaPromptHost } from "./PwaPrompts";
import { PushButton } from "./PushButton";
import { RouteMotion } from "./RouteMotion";

const RAIL_COLLAPSED_KEY = "ep.teacherRailCollapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(RAIL_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function TeacherShell() {
  const { user, signOut, isDevMockSession } = useAuth();
  const location = useLocation();
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

  const onClasses =
    location.pathname === "/teacher" || location.pathname.startsWith("/teacher/classes");
  const onAnalytics = location.pathname.startsWith("/teacher/analytics");
  const onAssistant = location.pathname.startsWith("/teacher/assistant");
  const onQuestions = location.pathname.startsWith("/teacher/questions");
  const onAtRisk = location.pathname.startsWith("/teacher/at-risk");
  const mobileItems = [
    { to: "/teacher", label: "My classes", active: onClasses },
    { to: "/teacher/analytics", label: "Analytics", active: onAnalytics },
    { to: "/teacher/assistant", label: "Assistant", active: onAssistant },
    { to: "/teacher/questions", label: "Question bank", active: onQuestions },
    { to: "/teacher/at-risk", label: "At-risk flags", active: onAtRisk },
  ];

  return (
    <div className="app-frame">
      <div className={`app ${railCollapsed ? "is-rail-collapsed" : ""}`}>
        <aside
          className={`rail ${railCollapsed ? "is-collapsed" : ""}`}
          aria-label="Primary"
          data-elevated="true"
        >
          <div className="rail__top">
            {!railCollapsed && (
              <Link to="/teacher" className="rail__brand">
                CurioEd
              </Link>
            )}
            <button
              type="button"
              className="rail__toggle"
              aria-expanded={!railCollapsed}
              aria-controls="teacher-rail-nav"
              title={railCollapsed ? "Expand navigation" : "Collapse navigation"}
              aria-label={railCollapsed ? "Expand navigation" : "Collapse navigation"}
              onClick={toggleRail}
            >
              {railCollapsed ? "»" : "«"}
            </button>
          </div>
          <nav id="teacher-rail-nav" className="rail__nav" aria-label="Teacher">
            <Link
              to="/teacher"
              className={`rail__link ${onClasses ? "is-active" : ""}`}
              title="My classes"
            >
              <span className="rail__link-short" aria-hidden="true">
                C
              </span>
              <span className="rail__link-label">My classes</span>
            </Link>
            <Link
              to="/teacher/analytics"
              className={`rail__link ${onAnalytics ? "is-active" : ""}`}
              title="Analytics"
            >
              <span className="rail__link-short" aria-hidden="true">
                D
              </span>
              <span className="rail__link-label">Analytics</span>
            </Link>
            <Link
              to="/teacher/assistant"
              className={`rail__link ${onAssistant ? "is-active" : ""}`}
              title="Assistant"
            >
              <span className="rail__link-short" aria-hidden="true">
                A
              </span>
              <span className="rail__link-label">Assistant</span>
            </Link>
            <Link
              to="/teacher/questions"
              className={`rail__link ${onQuestions ? "is-active" : ""}`}
              title="Question bank"
            >
              <span className="rail__link-short" aria-hidden="true">
                Q
              </span>
              <span className="rail__link-label">Question bank</span>
            </Link>
            <Link
              to="/teacher/at-risk"
              className={`rail__link ${onAtRisk ? "is-active" : ""}`}
              title="At-risk flags"
            >
              <span className="rail__link-short" aria-hidden="true">
                !
              </span>
              <span className="rail__link-label">At-risk flags</span>
            </Link>
          </nav>
          <div className="rail__user">
            <div className="rail__name">{user?.full_name ?? "Teacher"}</div>
            <div className="rail__meta">
              {[isDevMockSession ? "DEV mock" : null, "Teacher", "Demo School"]
                .filter(Boolean)
                .join(" · ")}
            </div>
            <PushButton variant="soft" size="sm" onClick={() => void signOut()}>
              Sign out
            </PushButton>
          </div>
        </aside>

        <div className="app__content">
          <MobileNav
            brandTo="/teacher"
            navLabel="Teacher"
            items={mobileItems}
            trailing={
              <PushButton variant="outline" size="sm" onClick={() => void signOut()}>
                Sign out
              </PushButton>
            }
          />

          <PwaPromptHost />

          <main className="main">
            <RouteMotion>
              <Outlet />
            </RouteMotion>
          </main>
        </div>
      </div>
    </div>
  );
}
