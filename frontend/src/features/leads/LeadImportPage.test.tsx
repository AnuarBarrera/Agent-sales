import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { LeadImportPage } from "./LeadImportPage";

const apiFetchMock = vi.fn();

vi.mock("../../lib/api", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

describe("LeadImportPage", () => {
  it("uploads a Google Sheets/n8n CSV and shows the import summary", async () => {
    apiFetchMock.mockResolvedValue({ created: 4, updated: 2, skipped: 1, errors: [] });

    render(
      <MemoryRouter>
        <LeadImportPage />
      </MemoryRouter>,
    );

    const file = new File(
      ["Place ID,Nombre\nabc,Negocio Demo\n"],
      "leads.csv",
      { type: "text/csv" },
    );
    fireEvent.change(screen.getByLabelText(/archivo csv/i), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /importar leads/i }));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(
      "/leads/import",
      expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
    ));
    expect(await screen.findByText("Nuevos")).toBeVisible();
    expect(screen.getByText("4")).toBeVisible();
    expect(screen.getByText("Actualizados")).toBeVisible();
    expect(screen.getByText("2")).toBeVisible();
  });
});
