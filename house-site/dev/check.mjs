// No build needed. Type-check application JS against official pinned Supabase types.
import { createRequire } from "node:module";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
const scratch =
  process.env.DISHWATCH_SCRATCH ||
  "/Volumes/Neural/dishwatch-scratch/houseleague/laneC";
const require = createRequire(`${scratch}/tooling/package.json`),
  ts = require("typescript");
const root = fileURLToPath(new URL("../", import.meta.url));
const files = readdirSync(`${root}/js`)
  .filter((f) => f.endsWith(".js"))
  .map((f) => `${root}/js/${f}`);
const options = {
  allowJs: true,
  checkJs: true,
  noEmit: true,
  strict: true,
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  lib: ["lib.es2022.d.ts", "lib.dom.d.ts", "lib.dom.iterable.d.ts"],
  skipLibCheck: true,
  baseUrl: root,
  paths: {
    "@supabase/supabase-js": [
      require
        .resolve("@supabase/supabase-js")
        .replace(/index\.cjs$/, "index.d.mts"),
    ],
  },
};
const program = ts.createProgram(files, options),
  diagnostics = ts.getPreEmitDiagnostics(program);
for (const d of diagnostics)
  console.log(
    `${d.file?.fileName}:${d.file ? d.file.getLineAndCharacterOfPosition(d.start).line + 1 : 0} ${ts.flattenDiagnosticMessageText(d.messageText, " ")}`,
  );
console.log(`${diagnostics.length} type diagnostics`);
process.exitCode = diagnostics.length ? 1 : 0;
