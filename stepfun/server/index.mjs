import express from "express";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { config } from "./config.mjs";
import {
  closeClient,
  collectionStats,
  getDbInfo,
  importItems,
  listCollectionStats,
  refreshCollection,
  resetCollection,
  searchCollection,
  ValidationError,
} from "./db.mjs";

const app = express();
app.use(express.json({ limit: "64mb" }));
app.use((req, res, next) => {
  const started = performance.now();
  res.on("finish", () => {
    console.log(
      `[api] ${req.method} ${req.originalUrl} -> ${res.statusCode} (${Math.round(performance.now() - started)}ms)`,
    );
  });
  next();
});

function route(handler) {
  return async (req, res, next) => {
    try {
      await handler(req, res);
    } catch (error) {
      if (error instanceof ValidationError) {
        error.status = 400;
      } else if (!error.status) {
        error.status = 503;
      }
      next(error);
    }
  };
}

app.get(
  "/api/health",
  route(async (_req, res) => {
    const collections = await listCollectionStats();
    res.json({ ok: true, db: getDbInfo(), collections });
  }),
);

app.get(
  "/api/collections",
  route(async (_req, res) => {
    res.json({ collections: await listCollectionStats() });
  }),
);

app.get(
  "/api/stats",
  route(async (req, res) => {
    const collection = String(req.query.collection ?? "");
    res.json(await collectionStats(collection));
  }),
);

app.post(
  "/api/import",
  route(async (req, res) => {
    const collection = String(req.body?.collection ?? "");
    const items = req.body?.items;
    const refresh = req.body?.refresh !== false;
    res.json(await importItems(collection, items, { refresh }));
  }),
);

app.post(
  "/api/search",
  route(async (req, res) => {
    const collection = String(req.body?.collection ?? "");
    res.json(await searchCollection(collection, req.body));
  }),
);

app.post(
  "/api/refresh",
  route(async (req, res) => {
    const collection = String(req.body?.collection ?? "");
    res.json(await refreshCollection(collection));
  }),
);

app.post(
  "/api/reset",
  route(async (req, res) => {
    const collection = String(req.body?.collection ?? "");
    res.json(await resetCollection(collection));
  }),
);

const indexHtml = resolve(config.dist, "index.html");
if (existsSync(indexHtml)) {
  app.use(express.static(config.dist, { index: false }));
  app.get("/benchmark", (_req, res) => res.sendFile(resolve(config.dist, "benchmark.html")));
  app.get("/benchmark.html", (_req, res) => res.sendFile(resolve(config.dist, "benchmark.html")));
  app.use((req, res, next) => {
    if (req.method !== "GET" || req.path.startsWith("/api") || req.path.includes(".")) {
      next();
      return;
    }
    res.sendFile(indexHtml);
  });
} else {
  app.get("/", (_req, res) => {
    res.json({
      message: "前端尚未构建。运行 npm run build 后重启，或使用 npm run dev 打开 http://127.0.0.1:5173",
    });
  });
}

app.use((req, res) => {
  res.status(404).json({ error: `未找到 ${req.method} ${req.path}` });
});

app.use((error, _req, res, _next) => {
  const status = Number(error?.status) || 500;
  console.error(`[api] error ${status}: ${error?.stack ?? error}`);
  res.status(status).json({ error: error?.message ?? "未知错误" });
});

const server = app.listen(config.port, () => {
  console.log(`stepfun API listening on http://127.0.0.1:${config.port}`);
  console.log(
    `seekdb target ${config.seekdb.host}:${config.seekdb.port}/${config.seekdb.database}`,
  );
});

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`received ${signal}, shutting down`);
  server.close(async () => {
    await closeClient();
    process.exit(0);
  });
  setTimeout(() => process.exit(0), 5000).unref();
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
