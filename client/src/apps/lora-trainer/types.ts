export interface DatasetImage {
  fileManagerId: number;
  name: string;
  // Empty for now — captioning will be its own step, added later.
  caption: string;
}

/**
 * One actual training image, derived from a `DatasetImage` after the People
 * step resolves crops. Most source photos produce exactly one entry
 * (`crop: null` if untouched, or a single person-box crop). A photo that
 * contains more than one instance of the trained person — e.g. a collage —
 * produces one entry per matched face instead, so each becomes its own
 * training image rather than only the best match being kept.
 *
 * `key` is stable and unique per entry (unlike `fileManagerId`, which
 * repeats across a split photo's entries) — use it for React list keys and
 * for targeting a specific entry's caption.
 */
export interface TrainingEntry {
  key: string;
  fileManagerId: number;
  name: string;
  crop: number[] | null;
  caption: string;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Job {
  id: number;
  status: JobStatus;
  trigger_word: string;
  base_model: string;
  steps: number;
  rank: number;
  alpha: number;
  error_message: string | null;
  output_file_id: number | null;
  created_at: string;
  updated_at: string;
  /** 0-1, or null while running but before the instance reports a step
   * (still syncing the dataset/loading the model), or for any non-running
   * status. */
  progress: number | null;
  /** Coarse phase ('syncing_dataset', 'loading_model') for the gap before
   * `progress` exists — null once progress is available, or for any
   * non-running status. */
  phase: string | null;
}
