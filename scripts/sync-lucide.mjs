import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";

const source = resolve("node_modules/lucide/dist/umd/lucide.min.js");
const target = resolve("aperio_agent_web/static/vendor/lucide/lucide.min.js");

mkdirSync(dirname(target), { recursive: true });
copyFileSync(source, target);
console.log(`Synced ${source} -> ${target}`);
