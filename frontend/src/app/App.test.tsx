import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";

vi.mock("../lib/api", () => ({
  apiFetch: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
}));

vi.mock("../lib/api", () => ({
  apiFetch: vi.fn().mockRejectedValue(new Error("unauthenticated")),
}));

// Mock MapSelector to avoid WebGL requirements
vi.mock("../features/prospection/MapSelector", () => ({
  MapSelector: () => <div data-testid="map-selector-mock">Map Selector Mock</div>,
}));

describe("App", () => {
  it("renders without crashing", async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible());
  });
});
