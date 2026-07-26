import { useActivityFeed } from "./useActivityFeed";

export function ActivityPage() {
  const { events } = useActivityFeed();

  return (
    <>
      <h2>Activity</h2>
      {events.length === 0 && <p className="activity-empty">Nothing logged yet.</p>}
      <ul className="activity-list">
        {events.map((event) => (
          <li key={event.id} className="activity-row">
            <span className="activity-time">
              {new Date(event.created_at).toLocaleString()}
            </span>
            <span className="activity-source">{event.source}</span>
            <span className={`activity-level level-${event.level}`}>{event.level}</span>
            <span className="activity-message">{event.message}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
