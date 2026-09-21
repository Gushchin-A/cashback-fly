import { defineConfig } from "vite";
import { readFileSync, statSync } from "node:fs";
import { resolve, normalize } from "node:path";

const SNAPSHOTS = resolve(__dirname, "..", "snapshots");

/**
 * В продакшене снимки отдаёт nginx из каталога воркера как /api/.
 * В разработке того же добивается этот плагин: читает файл с диска на каждый
 * запрос, без кеша, чтобы фронтенд видел ровно то, что пишет воркер.
 */
function snapshotsAsApi() {
  return {
    name: "snapshots-as-api",
    configureServer(server: any) {
      server.middlewares.use((req: any, res: any, next: any) => {
        if (!req.url?.startsWith("/api/")) return next();
        const relative = decodeURIComponent(req.url.slice("/api/".length).split("?")[0]);
        const file = normalize(resolve(SNAPSHOTS, relative));
        // Выход за пределы каталога снимков запрещён.
        if (!file.startsWith(SNAPSHOTS)) {
          res.statusCode = 403;
          return res.end("forbidden");
        }
        try {
          statSync(file);
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.setHeader("Cache-Control", "no-store");
          res.end(readFileSync(file));
        } catch {
          res.statusCode = 404;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify({ error: "нет снимка", path: relative }));
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [snapshotsAsApi()],
  server: { port: 5173, strictPort: true },
  build: { outDir: "dist", target: "es2022" },
});
