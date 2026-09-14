import { apiFetch } from "../../lib/api";

export type Role = "admin" | "setter" | "closer";
export type MemberStatus = "active" | "inactive";

export interface Member {
  id: string;
  user_id: string;
  role: Role;
  status: MemberStatus;
  user: { id: string; email: string; full_name: string };
}

export interface Invitation {
  id: string;
  email: string;
  role: "setter" | "closer";
  expires_at: string;
}

export interface Summary {
  unassigned_leads: number;
  overdue_tasks: number;
  due_today: number;
  stages: { id: string; key: string; label: string; lead_count: number }[];
  member_workload: { id: string; full_name: string; role: Role; lead_count: number }[];
}

export interface LeadItem {
  id: string;
  name: string;
  setter_name: string | null;
  closer_name: string | null;
}

export async function getSummary() { return apiFetch<Summary>("/admin/summary"); }
export async function getMembers() { return apiFetch<Member[]>("/workspaces/current/members"); }
export async function inviteMember(email: string, role: "setter" | "closer") {
  return apiFetch<Invitation>("/workspaces/current/invitations", { method: "POST", body: JSON.stringify({ email, role }) });
}
export async function updateMember(userId: string, update: { role?: Role; status?: MemberStatus }) {
  return apiFetch<Member>(`/workspaces/current/members/${userId}`, { method: "PATCH", body: JSON.stringify(update) });
}
export async function getLeads() {
  return apiFetch<{ items: LeadItem[] }>("/leads?limit=100");
}
export async function assignLeads(leadIds: string[], userId: string, role: "setter" | "closer") {
  return apiFetch("/leads/bulk-assign", { method: "POST", body: JSON.stringify({ lead_ids: leadIds, [`${role}_id`]: userId }) });
}
