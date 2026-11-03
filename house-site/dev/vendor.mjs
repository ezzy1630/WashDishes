// Reproduce the checked-in, pinned browser bundle using tooling outside the repo.
import { createRequire } from "node:module";
import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
const scratch =
  process.env.DISHWATCH_SCRATCH ||
  "/Volumes/Neural/dishwatch-scratch/houseleague/laneC";
const require = createRequire(`${scratch}/tooling/package.json`),
  { build } = require("esbuild");
const version = JSON.parse(
  await readFile(require.resolve("@supabase/supabase-js/package.json"), "utf8"),
).version;
if (version !== "2.118.0")
  throw new Error(
    "Install the pinned @supabase/supabase-js@2.118.0 before vendoring.",
  );
const output = new URL(`../vendor/supabase-js-${version}.js`, import.meta.url);
await build({
  entryPoints: [
    require
      .resolve("@supabase/supabase-js")
      .replace(/index\.cjs$/, "index.mjs"),
  ],
  bundle: true,
  format: "esm",
  platform: "browser",
  target: "es2022",
  minify: true,
  legalComments: "eof",
  outfile: output.pathname,
});
let licenses = "";
for (const name of [
  "@supabase/supabase-js",
  "@supabase/auth-js",
  "@supabase/functions-js",
  "@supabase/postgrest-js",
  "@supabase/realtime-js",
  "@supabase/storage-js",
  "iceberg-js",
  "tslib",
]) {
  const base = new URL(`file://${scratch}/tooling/node_modules/${name}/`),
    pkg = JSON.parse(await readFile(new URL("package.json", base), "utf8"));
  let license;
  for (const f of [
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "CopyrightNotice.txt",
  ]) {
    try {
      license = await readFile(new URL(f, base), "utf8");
      break;
    } catch {}
  }
  if (!license)
    throw new Error(
      `No license text for ${name}; inspect before redistributing.`,
    );
  licenses += `${name}@${pkg.version}\n${license}\n\n`;
}
await writeFile(
  new URL("../vendor/LICENSES.txt", import.meta.url),
  licenses.replace(/\r\n/g, "\n").trimEnd() + "\n",
);
console.log(
  `Bundled Supabase ${version}. SHA-256: ${createHash("sha256")
    .update(await readFile(output))
    .digest("hex")}`,
);
