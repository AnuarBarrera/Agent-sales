import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AdminRoute } from "./AdminRoute";
import { AdminDashboardPage } from "./AdminDashboardPage";
import { TeamPage } from "./TeamPage";
import { AssignmentsPage } from "./AssignmentsPage";
import { Layout } from "../../components/Layout";

const { authState, apiFetchMock } = vi.hoisted(() => ({
  authState: { user: { id: "u-1", email: "admin@example.test", full_name: "Ana Admin", role: "admin" as "admin" | "setter" | "closer" }, logout: vi.fn() },
  apiFetchMock: vi.fn(),
}));

vi.mock("../../features/auth/useAuth", () => ({ useAuth: () => authState }));
vi.mock("../../lib/api", () => ({ apiFetch: apiFetchMock }));

function renderInRouter(ui: React.ReactNode, path = "/admin") {
  return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
}

describe("administración", () => {
  beforeEach(() => {
    cleanup();
    authState.user.role = "admin";
    apiFetchMock.mockReset();
  });

  it("shows Administración only to admin", () => {
    authState.user.role = "admin";
    renderInRouter(<Layout />);
    expect(screen.getAllByRole("link", { name: /administración/i }).length).toBeGreaterThan(0);

    authState.user.role = "setter";
    cleanup();
    renderInRouter(<Layout />);
    expect(screen.queryByRole("link", { name: /administración/i })).not.toBeInTheDocument();
  });

  it("blocks setter on admin route", () => {
    authState.user.role = "setter";
    renderInRouter(<AdminRoute><p>contenido privado</p></AdminRoute>);
    expect(screen.getByText(/acceso restringido/i)).toBeVisible();
    expect(screen.queryByText("contenido privado")).not.toBeInTheDocument();
  });

  it("renders dashboard metrics from the admin summary", async () => {
    authState.user.role = "admin";
    apiFetchMock.mockResolvedValueOnce({ unassigned_leads: 3, overdue_tasks: 2, due_today: 5, stages: [], member_workload: [] });
    renderInRouter(<AdminDashboardPage />);
    expect(await screen.findByText("Leads sin responsable")).toBeVisible();
    expect(screen.getByText("3")).toBeVisible();
    expect(apiFetchMock).toHaveBeenCalledWith("/admin/summary");
  });

  it("invites a member and updates a member status", async () => {
    apiFetchMock.mockResolvedValueOnce([]).mockResolvedValueOnce({ id: "inv-1", email: "setter@example.test", role: "setter", expires_at: "2026-09-21T00:00:00Z" });
    renderInRouter(<TeamPage />);
    fireEvent.change(screen.getByLabelText(/correo/i), { target: { value: "setter@example.test" } });
    fireEvent.click(screen.getByRole("button", { name: /crear invitación/i }));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith(
      "/workspaces/current/invitations",
      expect.objectContaining({ method: "POST" }),
    ));
  });

  it("selects unassigned leads and assigns them in bulk", async () => {
    apiFetchMock.mockResolvedValueOnce({ items: [
      { id: "lead-1", name: "Negocio Norte", setter_name: null, closer_name: null },
      { id: "lead-2", name: "Negocio Centro", setter_name: "Setter", closer_name: null },
    ], next_cursor: null }).mockResolvedValueOnce({ unassigned_leads: 1, overdue_tasks: 0, due_today: 0, stages: [], member_workload: [] });
    renderInRouter(<AssignmentsPage />);
    expect(await screen.findByText("Negocio Norte")).toBeVisible();
    expect(screen.queryByText("Negocio Centro")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/seleccionar negocio norte/i));
    fireEvent.click(screen.getByRole("button", { name: /asignar seleccionados/i }));
    expect(screen.getByText(/1 lead seleccionado/i)).toBeVisible();
  });
});
