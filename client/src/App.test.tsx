import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

vi.stubGlobal(
  "EventSource",
  class {
    addEventListener() {}
    close() {}
  },
);

describe("App", () => {
  it("renders the dashboard heading and empty state before any alerts arrive", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ json: async () => [] }));

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByText("nithin-studio")).toBeInTheDocument();
    expect(await screen.findByText("Dashboard")).toBeInTheDocument();
  });

  it("navigates to the frame-extractor app on a real URL path", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ json: async () => [] }));

    render(
      <MemoryRouter initialEntries={["/services/frame-extractor"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText("frame-extractor")).toBeInTheDocument();
    expect(screen.getByText("Drop a video here")).toBeInTheDocument();
  });
});
