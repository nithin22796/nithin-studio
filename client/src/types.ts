export interface Alert {
  id: number;
  monitor_name: string;
  status: "up" | "down";
  message: string | null;
  received_at: string;
}
