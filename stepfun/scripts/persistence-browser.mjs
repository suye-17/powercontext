import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const baseUrl = (process.env.BASE_URL || "http://127.0.0.1:8787").replace(/\/$/, "");
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
await context.addInitScript(() => {
  Object.defineProperty(navigator, "gpu", { value: undefined, configurable: true });
});
const page = await context.newPage();
await page.goto(`${baseUrl}/`);
await page.waitForFunction(
  () => {
    const badge = document.querySelector(".badge");
    return badge && !badge.textContent.includes("模型加载中") && !badge.textContent.includes("模型不可用");
  },
  { timeout: 300000 },
);
const badge = (await page.locator(".badge").innerText()).replace(/\s+/g, " ").trim();
await page.getByPlaceholder(/例如/).fill("How long is the audit log kept?");
await page.getByRole("button", { name: "检索" }).click();
await page.locator(".results li").first().waitFor({ timeout: 120000 });
const resultText = await page.locator(".results").innerText();
if (!resultText.includes("rotation policy")) {
  throw new Error("persisted document was not retrieved by a browser query");
}
const stats = await page.locator(".stats").innerText();
const meta = await page.locator(".search-meta").innerText();
const payload = {
  label: "after-restart-browser",
  timestamp: new Date().toISOString(),
  badge,
  stats: stats.replace(/\s+/g, " ").trim(),
  searchMeta: meta.replace(/\s+/g, " ").trim(),
  resultText: resultText.slice(0, 1200),
};
const path = resolve(root, "evidence/persistence/after-restart-browser.json");
await mkdir(dirname(path), { recursive: true });
await writeFile(path, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
await page.screenshot({
  path: resolve(root, "evidence/persistence/after-restart-browser.png"),
  fullPage: true,
});
console.log(JSON.stringify(payload, null, 2));
await context.close();
await browser.close();
