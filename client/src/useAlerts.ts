import { useEffect, useState } from "react";
import type { Alert } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export function useAlerts() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let cancelled = false;

    fetch(`${API_URL}/alerts`)
      .then((res) => res.json())
      .then((data: Alert[]) => {
        if (!cancelled) setAlerts(data);
      });

    const source = new EventSource(`${API_URL}/events`);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.addEventListener("alert", (event) => {
      const alert = JSON.parse((event as MessageEvent).data) as Alert;
      setAlerts((prev) => [alert, ...prev]);
    });

    return () => {
      cancelled = true;
      source.close();
    };
  }, []);

  return { alerts, connected };
}
