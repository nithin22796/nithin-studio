import { useEffect, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export interface ActivityEvent {
  id: number;
  source: string;
  level: "warning" | "error";
  message: string;
  created_at: string;
}

export function useActivityFeed() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  // Only set by a live SSE push, never by the initial history fetch —
  // so consumers (e.g. a toast stack) don't replay the whole history on load.
  const [latestEvent, setLatestEvent] = useState<ActivityEvent | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetch(`${API_URL}/activity`)
      .then((res) => res.json())
      .then((data: ActivityEvent[]) => {
        if (!cancelled) setEvents(data);
      });

    const source = new EventSource(`${API_URL}/events`);
    source.addEventListener("activity", (event) => {
      const activity = JSON.parse((event as MessageEvent).data) as ActivityEvent;
      setEvents((prev) => [activity, ...prev]);
      setLatestEvent(activity);
    });

    return () => {
      cancelled = true;
      source.close();
    };
  }, []);

  return { events, latestEvent };
}
