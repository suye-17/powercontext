import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const baseUrl = (process.env.BASE_URL || "http://127.0.0.1:8787").replace(/\/$/, "");
const args = [
  "--enable-unsafe-webgpu",
  "--enable-features=Vulkan,WebGPUDeveloperFeatures",
  "--use-angle=vulkan",
  "--ignore-gpu-blocklist",
  "--disable-gpu-sandbox",
];

async function launch() {
  try {
    return await chromium.launch({ channel: "chromium", headless: true, args });
  } catch (error) {
    const { readdir, access } = await import("node:fs/promises");
    const cacheRoot = resolve(process.env.HOME ?? "", ".cache/ms-playwright");
    const entries = (await readdir(cacheRoot).catch(() => []))
      .filter((entry) => entry.startsWith("chromium-"))
      .sort()
      .reverse();
    for (const entry of entries) {
      const executablePath = resolve(cacheRoot, entry, "chrome-linux64/chrome");
      try {
        await access(executablePath);
        return await chromium.launch({ executablePath, headless: true, args });
      } catch {
        // try next
      }
    }
    throw error;
  }
}

const browser = await launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
const page = await context.newPage();
const origins = new Set();
page.on("request", (request) => {
  try {
    origins.add(new URL(request.url()).origin);
  } catch {
    // ignore non-http
  }
});
await page.goto(`${baseUrl}/`);
await page.waitForFunction(
  () => {
    const badge = document.querySelector(".badge");
    return badge && !badge.textContent.includes("模型加载中") && !badge.textContent.includes("模型不可用");
  },
  { timeout: 300000 },
);
const badge = (await page.locator(".badge").innerText()).replace(/\s+/g, " ").trim();
console.log("badge:", badge);
await page.getByPlaceholder(/例如/).fill("How long is the audit log kept?");
await page.getByRole("button", { name: "检索" }).click();
await page.locator(".results li").first().waitFor({ timeout: 120000 }).catch(() => {});
await mkdir(resolve(root, "evidence/screenshots"), { recursive: true });
await mkdir(resolve(root, "evidence/network"), { recursive: true });
await page.screenshot({ path: resolve(root, "evidence/screenshots/app-webgpu.png"), fullPage: true });
await writeFile(
  resolve(root, "evidence/network/webgpu-origins.json"),
  `${JSON.stringify([...origins].sort(), null, 2)}\n`,
  "utf-8",
);
console.log("origins:", [...origins].sort().join(", "));
await context.close();
await browser.close();
