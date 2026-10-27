import { mkdirSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

test("imports files incrementally and retrieves them through seekdb", async ({
  browser,
  request,
}) => {
  await request.post("/api/reset", { data: { collection: "stepfun_kb_int8" } });
  mkdirSync("evidence/network", { recursive: true });
  mkdirSync("evidence/screenshots", { recursive: true });

  const context = await browser.newContext();
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "gpu", { value: undefined, configurable: true });
  });
  const page = await context.newPage();
  const origins = new Set<string>();
  page.on("request", (requestEvent) => {
    try {
      origins.add(new URL(requestEvent.url()).origin);
    } catch {
      // ignore non-http requests
    }
  });

  await page.goto("/");
  await expect(page.locator(".badge")).toContainText("WASM", { timeout: 180000 });

  await page.setInputFiles("input[type=file]", {
    name: "demo.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      "seekdb stores vectors and source text in one collection. " +
        "The knowledge base writes embeddings from the browser and refreshes the index after import.",
    ),
  });
  await expect(page.locator(".stats > div").nth(1).locator("dd")).toHaveText("1", {
    timeout: 180000,
  });

  await page.getByPlaceholder(/例如/).fill("Where does seekdb store vectors and text?");
  await page.getByRole("button", { name: "检索" }).click();
  await expect(page.locator(".results li").first()).toBeVisible({ timeout: 60000 });
  await expect(page.locator(".results")).toContainText("seekdb stores vectors");
  await expect(page.locator(".search-meta")).toContainText("混合检索");
  await expect(page.locator(".search-meta")).toContainText("关键词");

  await page.setInputFiles("input[type=file]", {
    name: "audit.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(
      "The rotation policy keeps the audit log for ninety days. " +
        "Audit records are immutable and can be exported by the compliance team.",
    ),
  });
  await expect(page.locator(".stats > div").nth(1).locator("dd")).toHaveText("2", {
    timeout: 180000,
  });

  await page.getByPlaceholder(/例如/).fill("How long is the audit log kept?");
  await page.getByRole("button", { name: "检索" }).click();
  await expect(page.locator(".results")).toContainText("rotation policy", {
    timeout: 60000,
  });

  writeFileSync(
    "evidence/network/e2e-origins.json",
    `${JSON.stringify([...origins].sort(), null, 2)}\n`,
    "utf-8",
  );
  await page.screenshot({
    path: "evidence/screenshots/e2e-wasm-search.png",
    fullPage: true,
  });
  await context.close();
});
