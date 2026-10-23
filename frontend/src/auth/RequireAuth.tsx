import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "./AuthContext";

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading, sessionError, refreshProfile } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="center-state" role="status">
        Loading…
      </div>
    );
  }

  if (!user && sessionError) {
    return (
      <div className="center-state" role="alert">
        <p>{sessionError}</p>
        <button type="button" onClick={() => void refreshProfile()}>
          Retry
        </button>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <>{children}</>;
}
