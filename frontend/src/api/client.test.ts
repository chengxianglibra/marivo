import { afterEach, describe, expect, it, vi } from "vitest";

describe("api client URL handling", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it("routes relative /api bases through the current origin", async () => {
    vi.stubEnv("VITE_MARIVO_USE_MOCKS", "false");
    vi.stubEnv("VITE_MARIVO_API_BASE_URL", "/api");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: "ok" })));
    vi.stubGlobal("fetch", fetchMock);

    const { apiClient } = await import("./client");
    await apiClient.get("/health");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:3000/api/health",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("preserves absolute backend bases", async () => {
    vi.stubEnv("VITE_MARIVO_USE_MOCKS", "false");
    vi.stubEnv("VITE_MARIVO_API_BASE_URL", "http://localhost:8000");
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: "ok" })));
    vi.stubGlobal("fetch", fetchMock);

    const { apiClient } = await import("./client");
    await apiClient.get("/health", { verbose: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/health?verbose=true",
      expect.objectContaining({ method: "GET" }),
    );
  });
});
