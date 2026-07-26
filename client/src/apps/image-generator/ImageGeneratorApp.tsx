import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import * as fileManagerApi from "../file-manager/api";
import * as api from "./api";
import type { GeneratedImage, Session } from "./types";
import "./ImageGeneratorApp.css";

const POLL_INTERVAL_MS = 4000;
const NON_TERMINAL_STATUSES = new Set(["launching", "running", "stopping"]);

export function ImageGeneratorApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [steps, setSteps] = useState(20);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [images, setImages] = useState<GeneratedImage[]>([]);

  async function refresh() {
    const current = await api.getCurrentSession();
    // A session that just flipped to "failed" stops showing up as "current"
    // on the *next* poll (the backend only tracks non-terminal sessions as
    // active) — capture its error message on the poll that actually
    // observes "failed", before it disappears, so the UI doesn't just
    // silently revert to "no session" with no explanation.
    if (current && current.status === "failed") {
      setLastError(current.error_message ?? "unknown error");
    }
    setSession(current);
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time fetch of the current session on mount, not a render-driven state sync
    void refresh();
    void api.listModels().then((found) => {
      setModels(found);
      setSelectedModel((prev) => prev || found[0] || "");
    });
  }, []);

  useEffect(() => {
    if (!session || !NON_TERMINAL_STATUSES.has(session.status)) return;
    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [session]);

  async function handleStart() {
    if (!selectedModel) return;
    setLastError(null);
    setImages([]);
    setStarting(true);
    try {
      const started = await api.startSession(selectedModel);
      setSession(started);
    } catch (err) {
      setLastError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setStarting(false);
    }
  }

  async function handleStop() {
    if (!session) return;
    setStopping(true);
    try {
      const stopped = await api.stopSession(session.id);
      setSession(stopped);
    } catch (err) {
      setLastError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setStopping(false);
    }
  }

  async function handleGenerate(event: React.FormEvent) {
    event.preventDefault();
    if (!prompt.trim()) return;
    setGenerating(true);
    setGenerateError(null);
    try {
      const image = await api.generate(prompt.trim(), steps);
      setImages((prev) => [image, ...prev]);
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setGenerating(false);
    }
  }

  const status = session?.status;
  const isRunning = status === "running";
  const notStarted = !session || status === "terminated" || status === "failed";

  return (
    <div className="image-generator">
      <Link className="back-link" to="/services">
        ← Services
      </Link>
      <h2>image-generator</h2>

      {notStarted && (
        <div className="ig-start-panel">
          <p className="ig-start-blurb">
            Launches a real cloud GPU instance (~1-2 min to boot) loaded with
            the model you pick below. Generate as many images as you like once
            it's running, then stop it when you're done — nothing is billed
            while it's not running.
          </p>
          <div className="ig-start-controls">
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              disabled={starting || models.length === 0}
            >
              {models.length === 0 && <option value="">No models found</option>}
              {models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={starting || !selectedModel}
              onClick={() => void handleStart()}
            >
              {starting ? "Starting…" : "Start"}
            </button>
          </div>
        </div>
      )}

      {session && !notStarted && (
        <div className="ig-session-bar">
          <span className={`ig-status ig-status-${status}`}>
            {status === "launching" && "Starting instance…"}
            {status === "running" && "Running"}
            {status === "stopping" && "Stopping…"}
          </span>
          {isRunning && (
            <button
              type="button"
              disabled={stopping}
              onClick={() => void handleStop()}
            >
              {stopping ? "Stopping…" : "Stop"}
            </button>
          )}
        </div>
      )}

      {lastError && <p className="error-message">{lastError}</p>}

      {isRunning && (
        <form
          className="ig-prompt-form"
          onSubmit={(e) => void handleGenerate(e)}
        >
          <textarea
            placeholder="Describe the image you want…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
          />
          <div className="ig-prompt-controls">
            <label>
              Steps
              <input
                type="number"
                min={1}
                max={100}
                value={steps}
                onChange={(e) => setSteps(Number(e.target.value))}
              />
            </label>
            <button type="submit" disabled={generating || !prompt.trim()}>
              {generating ? "Generating…" : "Generate"}
            </button>
          </div>
          {generateError && <p className="error-message">{generateError}</p>}
        </form>
      )}

      {images.length > 0 && (
        <div className="ig-gallery">
          {images.map((image) => (
            <a
              key={image.file_id}
              href={fileManagerApi.fileContentUrl(image.file_id, "attachment")}
              className="ig-gallery-item"
            >
              <img
                src={fileManagerApi.fileContentUrl(image.file_id, "inline")}
                alt={image.name}
              />
              <span>{image.name}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
