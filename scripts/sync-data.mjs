/**
 * 把编译好的静态分片同步进网页产物目录，使 `apps/web/dist` 成为可直接托管的完整交付物。
 *
 * 说明：vite 构建会清空 apps/web/dist（emptyOutDir），因此必须在 web 构建之后执行本步骤。
 * 产物本身（dist_data/ 与 apps/web/dist/）已在 .gitignore 中，不进入版本库。
 */
import { cpSync, existsSync, rmSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "dist_data");
const target = join(root, "apps", "web", "dist", "data");

if (!existsSync(source)) {
  console.error(`[sync-data] 缺少编译产物目录：${source}（请先执行 npm run build:data）`);
  process.exit(1);
}

rmSync(target, { recursive: true, force: true });
cpSync(source, target, { recursive: true });

const size = statSync(join(target, "v1", "manifest.json")).size;
console.log(`[sync-data] 已同步静态分片 -> ${target}（manifest ${size} bytes）`);
