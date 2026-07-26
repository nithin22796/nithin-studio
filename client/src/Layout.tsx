import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAlerts } from "./useAlerts";
import { useActivityFeed } from "./useActivityFeed";
import { PulseTrace } from "./PulseTrace";
import * as imageGeneratorApi from "./apps/image-generator/api";
import type { Session as GeneratorSession } from "./apps/image-generator/types";
import type { Alert } from "./types";

const GENERATOR_POLL_INTERVAL_MS = 4000;
const GENERATOR_NON_TERMINAL = new Set(["launching", "running", "stopping"]);

export interface DashboardContext {
  alerts: Alert[];
}

function latestStatusPerMonitor(alerts: Alert[]): Alert[] {
  const seen = new Set<string>();
  const latest: Alert[] = [];
  for (const alert of alerts) {
    if (!seen.has(alert.monitor_name)) {
      seen.add(alert.monitor_name);
      latest.push(alert);
    }
  }
  return latest;
}

export function Layout() {
  const { alerts, connected } = useAlerts();
  const { events, latestEvent } = useActivityFeed();
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  // "Clear all" only hides events from this tray, up to whatever was newest
  // at the time — it doesn't delete anything server-side, since `events`
  // is shared with the permanent `/activity` page (the actual history log).
  const [clearedBeforeId, setClearedBeforeId] = useState<number | null>(null);
  const visibleEvents =
    clearedBeforeId === null
      ? events
      : events.filter((e) => e.id > clearedBeforeId);
  const [generatorSession, setGeneratorSession] =
    useState<GeneratorSession | null>(null);
  const [generatorPanelOpen, setGeneratorPanelOpen] = useState(false);
  const [generatorStopping, setGeneratorStopping] = useState(false);
  const statuses = latestStatusPerMonitor(alerts);
  const overallStatus = statuses.some((s) => s.status === "down")
    ? "down"
    : "up";

  // Bumps the badge whenever a new event arrives live via SSE — not on the
  // initial history fetch, since `latestEvent` is only ever set by a push
  // (see `useActivityFeed`), so opening the app doesn't show a badge for
  // everything that already happened before this session existed.
  useEffect(() => {
    if (!latestEvent) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- increments the badge whenever a new live activity event arrives
    setUnreadCount((c) => c + 1);
  }, [latestEvent]);

  function toggleNotifications() {
    setNotificationsOpen((open) => !open);
    setUnreadCount(0);
  }

  function clearAllNotifications() {
    if (events.length > 0) setClearedBeforeId(events[0].id);
  }

  // This widget is global (unlike the notification tray, it's fed by
  // polling, not SSE) purely so the instance's up/down status is visible
  // and stoppable from anywhere in the app, not just the image-generator
  // page itself.
  async function refreshGeneratorSession() {
    // Best-effort — this is a background poll running on every page, not a
    // user-initiated action, so a transient failure shouldn't surface an
    // error anywhere; it just tries again on the next interval tick.
    try {
      setGeneratorSession(await imageGeneratorApi.getCurrentSession());
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time fetch of the current session on mount, not a render-driven state sync
    void refreshGeneratorSession();
  }, []);

  useEffect(() => {
    if (
      !generatorSession ||
      !GENERATOR_NON_TERMINAL.has(generatorSession.status)
    )
      return;
    const timer = setInterval(
      () => void refreshGeneratorSession(),
      GENERATOR_POLL_INTERVAL_MS,
    );
    return () => clearInterval(timer);
  }, [generatorSession]);

  async function stopGeneratorSession() {
    if (!generatorSession) return;
    setGeneratorStopping(true);
    try {
      setGeneratorSession(
        await imageGeneratorApi.stopSession(generatorSession.id),
      );
    } finally {
      setGeneratorStopping(false);
    }
  }

  const generatorStatus = generatorSession?.status ?? "stopped";
  const generatorIsUp =
    generatorStatus === "running" || generatorStatus === "launching";

  return (
    <div className="app-shell">
      <header className="topbar">
        <h1>nithin-studio</h1>

        <div className="topbar-status">
          <PulseTrace live={connected} />
          <span className={`overall-readout status-${overallStatus}`}>
            <span className="overall-dot" />
            {overallStatus === "up" ? "all systems up" : "attention needed"}
          </span>
          <span
            data-testid="connection-status"
            className={connected ? "connected" : "disconnected"}
          >
            {connected ? "live" : "connecting…"}
          </span>
          <button
            type="button"
            className="notification-bell"
            aria-label="Image-generator instance status"
            onClick={() => setGeneratorPanelOpen((open) => !open)}
          >
            <span
              className={`instance-dot ${generatorIsUp ? "instance-up" : "instance-down"}`}
            />
            ☁️
          </button>
          <button
            type="button"
            className="notification-bell"
            aria-label="Notifications"
            onClick={toggleNotifications}
          >
            🔔
            {unreadCount > 0 && (
              <span className="notification-badge">
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}
          </button>
        </div>
      </header>

      <div className="body">
        <nav className="sidebar">
          <span className="sidebar-label">Console</span>
          <ul className="nav-list">
            <li>
              <NavLink
                to="/dashboard"
                className={({ isActive }) =>
                  `nav-item ${isActive ? "active" : ""}`
                }
              >
                Dashboard
              </NavLink>
            </li>
            <li>
              <NavLink
                to="/services"
                className={({ isActive }) =>
                  `nav-item ${isActive ? "active" : ""}`
                }
              >
                Services
              </NavLink>
            </li>
            <li>
              <NavLink
                to="/activity"
                className={({ isActive }) =>
                  `nav-item ${isActive ? "active" : ""}`
                }
              >
                Activity
              </NavLink>
            </li>
          </ul>
        </nav>

        <main className="content">
          <Outlet context={{ alerts } satisfies DashboardContext} />
        </main>
      </div>

      {notificationsOpen && (
        <div
          className="notification-backdrop"
          onClick={() => setNotificationsOpen(false)}
        >
          <aside
            className="notification-panel"
            onClick={(e) => e.stopPropagation()}
            aria-label="Notifications"
          >
            <div className="notification-panel-header">
              <h2>Notifications</h2>
              <div className="notification-panel-header-actions">
                {visibleEvents.length > 0 && (
                  <button
                    type="button"
                    className="notification-clear-all"
                    onClick={clearAllNotifications}
                  >
                    Clear all
                  </button>
                )}
                <button
                  type="button"
                  className="notification-panel-close"
                  aria-label="Close"
                  onClick={() => setNotificationsOpen(false)}
                >
                  ✕
                </button>
              </div>
            </div>

            {visibleEvents.length === 0 && (
              <p className="notification-empty">Nothing yet.</p>
            )}

            <ul className="notification-list">
              {visibleEvents.map((event) => (
                <li key={event.id} className="notification-row">
                  <div className="notification-row-top">
                    <span className={`notification-level level-${event.level}`}>
                      {event.level}
                    </span>
                    <span className="notification-source">{event.source}</span>
                    <span className="notification-time">
                      {new Date(event.created_at).toLocaleTimeString()}
                    </span>
                  </div>
                  <p className="notification-message">{event.message}</p>
                </li>
              ))}
            </ul>

            <NavLink
              to="/activity"
              className="notification-panel-link"
              onClick={() => setNotificationsOpen(false)}
            >
              View full activity log →
            </NavLink>
          </aside>
        </div>
      )}

      {generatorPanelOpen && (
        <div
          className="notification-backdrop"
          onClick={() => setGeneratorPanelOpen(false)}
        >
          <aside
            className="notification-panel instance-panel"
            onClick={(e) => e.stopPropagation()}
            aria-label="Image-generator instance"
          >
            <div className="notification-panel-header">
              <h2>Image-generator instance</h2>
              <button
                type="button"
                className="notification-panel-close"
                aria-label="Close"
                onClick={() => setGeneratorPanelOpen(false)}
              >
                ✕
              </button>
            </div>

            <div className="instance-panel-body">
              <span
                className={`overall-readout status-${generatorIsUp ? "up" : "down"}`}
              >
                <span className="overall-dot" />
                {generatorStatus === "running" && "running"}
                {generatorStatus === "launching" && "starting…"}
                {generatorStatus === "stopping" && "stopping…"}
                {generatorStatus === "stopped" && "not running"}
                {generatorStatus === "failed" && "failed to start"}
                {generatorStatus === "terminated" && "not running"}
              </span>

              {generatorSession?.error_message &&
                generatorStatus === "failed" && (
                  <p className="error-message">
                    {generatorSession.error_message}
                  </p>
                )}

              {generatorIsUp ? (
                <button
                  type="button"
                  disabled={generatorStopping}
                  onClick={() => void stopGeneratorSession()}
                >
                  {generatorStopping ? "Stopping…" : "Stop"}
                </button>
              ) : (
                <NavLink
                  to="/services/image-generator"
                  onClick={() => setGeneratorPanelOpen(false)}
                >
                  Start a session →
                </NavLink>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
