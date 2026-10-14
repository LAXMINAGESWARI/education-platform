import { Link, Outlet, useLocation } from "react-router-dom";
import { useState } from "react";

import { useAuth } from "../auth/AuthContext";
import { PushButton } from "./PushButton";
import { RouteMotion } from "./RouteMotion";

const RAIL_COLLAPSED_KEY = "ep.adminRailCollapsed";

/** The admin sections, in menu order. The side rail and the mobile bar both render from this
 * list, so adding a page means adding one entry here. `short` is the label shown when the
 * rail is collapsed. */
const NAV_ITEMS = [
  { to: "/admin/dashboard", label: "Dashboard", short: "⌂" },
  { to: "/admin/materials", label: "Materials", short: "M" },
  { to: "/admin/documents", label: "Documents", short: "D" },
  { to: "/admin/policy", label: "Policy assistant", short: "P" },
  { to: "/admin/at-risk", label: "At-risk flags", short: "!" },
] as const;

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(RAIL_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function AdminShell() {
  const { user, signOut, isDevMockSession } = useAuth();
  const location = useLocation();
  // Read the saved preference on the first render, so a collapsed rail does not flash open.
  const [railCollapsed, setRailCollapsed] = useState(readCollapsed);

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

  function linkClass(to: string): string {
    return `rail__link ${location.pathname.startsWith(to) ? "is-active" : ""}`;
  }

  return (
    <div className="app-frame">
      <div className="demo-banner" role="status">
        {isDevMockSession
          ? "Demo mockup only · Admin fixture session · materials need a real JWT"
          : "Demo mockup only · Admin · live published materials API"}
      </div>
      <div className={`app ${railCollapsed ? "is-rail-collapsed" : ""}`}>
        <aside
          className={`rail ${railCollapsed ? "is-collapsed" : ""}`}
          aria-label="Admin primary"
          data-elevated="true"
        >
          <div className="rail__top">
            {!railCollapsed && (
              <Link to="/admin/dashboard" className="rail__brand">
                Education Platform
              </Link>
            )}
            <button
              type="button"
              className="rail__toggle"
              aria-expanded={!railCollapsed}
              aria-controls="admin-rail-nav"
              title={railCollapsed ? "Expand navigation" : "Collapse navigation"}
              aria-label={railCollapsed ? "Expand navigation" : "Collapse navigation"}
              onClick={toggleRail}
            >
              {railCollapsed ? "»" : "«"}
            </button>
          </div>
          <nav id="admin-rail-nav" className="rail__nav" aria-label="Admin">
            {NAV_ITEMS.map((item) => (
              <Link key={item.to} to={item.to} className={linkClass(item.to)} title={item.label}>
                <span className="rail__link-short" aria-hidden="true">
                  {item.short}
                </span>
                <span className="rail__link-label">{item.label}</span>
              </Link>
            ))}
          </nav>
          <div className="rail__user">
            <div className="rail__name">{user?.full_name ?? "Administrator"}</div>
            <div className="rail__meta">Administrator · Demo School</div>
            <PushButton variant="soft" size="sm" onClick={() => void signOut()}>
              Sign out
            </PushButton>
          </div>
        </aside>

        <div className="app__content">
          <div className="topbar">
            <div className="topbar__row">
              <Link to="/admin/dashboard" className="topbar__brand">
                Education Platform
              </Link>
              <PushButton variant="outline" size="sm" onClick={() => void signOut()}>
                Sign out
              </PushButton>
            </div>
            <nav className="rail__nav rail__nav--horizontal" aria-label="Mobile admin">
              {NAV_ITEMS.map((item) => (
                <Link key={item.to} to={item.to} className={linkClass(item.to)}>
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>

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
