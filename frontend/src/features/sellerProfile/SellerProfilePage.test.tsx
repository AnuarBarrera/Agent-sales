import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { SellerProfilePage } from "./SellerProfilePage";

const apiFetchMock = vi.fn();

vi.mock("../../lib/api", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

describe("SellerProfilePage", () => {
  it("loads the seller profile and saves changes for the workspace", async () => {
    apiFetchMock.mockResolvedValueOnce({
      id: "profile-1",
      workspace_id: "workspace-1",
      business_name: "Tu Web MX",
      description: "Agencia de sitios web",
      offer_summary: "Sitios web para negocios locales",
      target_customer_summary: "Negocios locales",
      location_or_market: "México",
      price_context: "Desde $2,500 MXN",
      primary_goal: "Conseguir conversaciones",
      differentiators: "Implementación rápida",
      sales_constraints: "No prometer resultados garantizados",
      created_at: "2026-09-14T00:00:00Z",
      updated_at: "2026-09-14T00:00:00Z",
    });
    apiFetchMock.mockResolvedValueOnce({ business_name: "Tu Web MX renovada" });

    render(
      <MemoryRouter>
        <SellerProfilePage />
      </MemoryRouter>,
    );

    const businessName = await screen.findByLabelText(/Nombre del negocio/);
    expect(businessName).toHaveValue("Tu Web MX");

    fireEvent.change(businessName, { target: { value: "Tu Web MX renovada" } });
    fireEvent.click(screen.getByRole("button", { name: "Guardar perfil" }));

    await waitFor(() => expect(apiFetchMock).toHaveBeenLastCalledWith(
      "/seller-profile",
      expect.objectContaining({
        method: "PUT",
        body: expect.stringContaining('"business_name":"Tu Web MX renovada"'),
      }),
    ));
    expect(await screen.findByRole("status")).toHaveTextContent("Perfil guardado");
  });
});
