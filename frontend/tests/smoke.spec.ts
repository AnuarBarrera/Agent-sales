import { test, expect } from "@playwright/test";

test("página de acceso del CRM carga", async ({ page }) => {
  await page.goto("/login");
  await expect(page).toHaveTitle("Tu Web MX CRM");
  await expect(page.getByRole("heading", { name: "Iniciar sesión" })).toBeVisible();
});
