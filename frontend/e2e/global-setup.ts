import fs from "node:fs";
import path from "node:path";

export default function globalSetup() {
  const dir = path.resolve(import.meta.dirname, "artifacts");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "usecase-checks.json"), "[]\n");
}
