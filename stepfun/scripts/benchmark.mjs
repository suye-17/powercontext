import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const argv = process.argv.slice(2);

function option(name, fallback) {
  const prefix = `--${name}=`;
  const found = argv.find((value) => value.startsWith(prefix));
  return found ? found.slice(prefix.length) : fallback;
}

const mode = option("mode", "wasm");
if (!["wasm", "webgpu"].includes(mode)) {
  console.error("--mode must be wasm or webgpu");
  process.exit(2);
}

const configId = option("config", mode === "webgpu" ? "fp16-webgpu" : "int8-wasm");
const baseUrl = option("base-url", "http://127.0.0.1:8787").replace(/\/$/, "");
const out = resolve(root, option("out", `evidence/benchmarks/benchmark-${configId}-${mode}.json`));
const timeoutMs = Number(option("timeout-ms", String(30 * 60 * 1000)));

const webgpuArgs = [
  "--enable-unsafe-webgpu",
  "--enable-features=Vulkan,WebGPUDeveloperFeatures",
  "--use-angle=vulkan",
  "--ignore-gpu-blocklist",
  "--disable-gpu-sandbox",
];

async function launchBrowser() {
  if (mode === "wasm") {
    try {
      return await chromium.launch({ channel: "chromium", headless: true });
    } catch {
      return chromium.launch({ headless: true });
    }
  }
  try {
    return await chromium.launch({
      channel: "chromium",
      headless: true,
      args: webgpuArgs,
    });
  } catch (channelError) {
    console.warn(`channel chromium failed (${channelError.message}); trying executable path`);
    const { readdir } = await import("node:fs/promises");
    const cacheRoot = resolve(process.env.HOME ?? "", ".cache/ms-playwright");
    const entries = await readdir(cacheRoot).catch(() => []);
    const candidates = entries
      .filter((entry) => entry.startsWith("chromium-"))
      .sort()
      .reverse();
    for (const entry of candidates) {
      const executablePath = resolve(cacheRoot, entry, "chrome-linux64/chrome");
      try {
        await import("node:fs/promises").then(({ access }) => access(executablePath));
        return await chromium.launch({ executablePath, headless: true, args: webgpuArgs });
      } catch {
        // try the next cached Chromium
      }
    }
    throw channelError;
  }
}

function firstLine(value) {
  return String(value ?? "").split("\n")[0];
}

let browser;
let context;
try {
  browser = await launchBrowser();
  context = await browser.newContext({ viewport: { width: 1280, height: 960 } });
  if (mode === "wasm") {
    await context.addInitScript(() => {
      Object.defineProperty(navigator, "gpu", { value: undefined, configurable: true });
    });
  }
  const page = await context.newPage();
  const url = `${baseUrl}/benchmark.html?config=${encodeURIComponent(configId)}&autorun=1`;
  console.log(`opening ${url}`);
  await page.goto(url, { waitUntil: "domcontentloaded" });

  const started = Date.now();
  let result = null;
  let failure = null;
  while (Date.now() - started < timeoutMs) {
    const state = await page.evaluate(() => ({
      result: window.__BENCH_RESULT__ ?? null,
      error: document.querySelector(".error")?.textContent ?? null,
    }));
    if (state.result?.length) {
      result = state.result;
      break;
    }
    if (state.error) {
      failure = state.error;
      break;
    }
    await page.waitForTimeout(2000);
  }

  const log = await page.locator(".bench-log").textContent().catch(() => "");
  await mkdir(dirname(out), { recursive: true });
  await writeFile(`${out.replace(/\.json$/, "")}.log`, log ?? "", "utf-8");
  await page.screenshot({
    path: resolve(root, `evidence/screenshots/benchmark-${configId}-${mode}.png`),
    fullPage: true,
  });

  if (!result) {
    console.error(`benchmark did not produce a result: ${firstLine(failure) || "timeout"}`);
    process.exitCode = 1;
  } else {
    await writeFile(out, `${JSON.stringify(result, null, 2)}\n`, "utf-8");
    const row = result[0];
    console.log(
      `wrote ${out}\nRecall@5 ${(row.quality.recall * 100).toFixed(1)}% | ` +
        `nDCG@5 ${(row.quality.ndcg * 100).toFixed(1)}% | ` +
        `E2E ${row.timings.endToEndMsMedian.toFixed(1)} ms | ` +
        `adapter ${row.environment.webgpuAdapter ?? "none"}`,
    );
  }
} catch (error) {
  console.error(firstLine(error));
  process.exitCode = 1;
} finally {
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
}
