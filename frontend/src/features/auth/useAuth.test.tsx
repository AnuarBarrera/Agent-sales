import { describe, expect, it } from "vitest";
import { normalizeAuthUser } from "./useAuth";

describe("normalizeAuthUser", () => {
  it("derives the current role from the membership response", () => {
    const result = normalizeAuthUser({
      user: { id: "user-1", email: "admin@example.com", full_name: "Admin" },
      membership: { role: "admin" },
    });

    expect(result).toEqual({
      id: "user-1",
      email: "admin@example.com",
      full_name: "Admin",
      role: "admin",
    });
  });
});
