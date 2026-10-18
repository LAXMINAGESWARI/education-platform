import { AtRiskTierBadge } from "./AtRiskTierBadge";
import { PushButton } from "./PushButton";
import type { AtRiskDriver, AtRiskFlag } from "../api/atRisk";

const METRIC_LABEL: Record<string, string> = {
  mastery_percent: "Mastery",
  mastery_trend: "Mastery trend",
  attendance_percent: "Attendance",
};

/**
 * "Mastery: 56.0% (below 60.0)" -- a level driver's own `comparison` names only the
 * threshold it failed ("below 60.0"), never the student's actual number, so this adds
 * `driver.value` in front of it. A trend driver's `comparison` already states its value
 * ("declined 22.5 points (threshold 15.0)"), so it is used as-is rather than doubled up.
 * Never fabricates a reason beyond what the engine actually named (AR-1).
 *
 * One decimal place, matching the threshold's own precision -- rounding a value like
 * 59.6 to a whole "60%" would read as "60% (below 60.0)", contradicting the very
 * comparison it sits next to.
 */
function driverHeadline(driver: AtRiskDriver): string {
  const label = METRIC_LABEL[driver.metric] ?? driver.metric;
  if (driver.metric === "mastery_trend") {
    return `${label}: ${driver.comparison}`;
  }
  return `${label}: ${driver.value.toFixed(1)}% (${driver.comparison})`;
}

type AtRiskFlagsTableProps = {
  flags: AtRiskFlag[];
  busyId: string | null;
  onDismiss: (flag: AtRiskFlag) => void;
};

/** Shared by the teacher and admin at-risk pages -- same columns, same dismiss action.
 * `subject` renders as "Attendance" for the whole-student flags only an administrator can
 * ever receive (spec Section 7.2); a teacher's flags never have a null subject. */
export function AtRiskFlagsTable({ flags, busyId, onDismiss }: AtRiskFlagsTableProps) {
  return (
    <div className="table-scroll">
      <table className="data-table data-table--cards">
        <thead>
          <tr>
            <th scope="col">Student</th>
            <th scope="col">Subject</th>
            <th scope="col">Tier</th>
            <th scope="col">Why</th>
            <th scope="col">Action</th>
          </tr>
        </thead>
        <tbody>
          {flags.map((flag) => (
            <tr key={flag.id} className={flag.tier === "urgent" ? "at-risk-row--urgent" : ""}>
              <td data-label="Student">{flag.student_name}</td>
              <td data-label="Subject">{flag.subject ?? <span className="badge">Attendance</span>}</td>
              <td data-label="Tier">
                <AtRiskTierBadge tier={flag.tier} />
              </td>
              <td className="at-risk-why" data-label="Why">
                <ul className="at-risk-drivers">
                  {flag.drivers.map((driver, index) => (
                    <li key={index}>
                      {driverHeadline(driver)}
                      {driver.metric === "mastery_trend" && (
                        <span className="at-risk-drivers__detail"> · {driver.window}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </td>
              <td data-label="Action">
                <PushButton
                  variant="outline"
                  size="sm"
                  onClick={() => onDismiss(flag)}
                  disabled={busyId === flag.id}
                >
                  Dismiss
                </PushButton>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
