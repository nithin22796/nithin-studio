import { useOutletContext } from "react-router-dom";
import type { DashboardContext } from "./Layout";

export function DashboardPage() {
  const { alerts } = useOutletContext<DashboardContext>();

  return (
    <>
      <h2>Live feed</h2>
      <ul className="history-list">
        {alerts.map((alert) => (
          <li key={alert.id}>
            <span className="time">
              {new Date(alert.received_at).toLocaleString()}
            </span>
            <span className="name">{alert.monitor_name}</span>
            <span className={`status-${alert.status}`}>{alert.status}</span>
            {alert.message && <span className="message">{alert.message}</span>}
          </li>
        ))}
      </ul>
    </>
  );
}
