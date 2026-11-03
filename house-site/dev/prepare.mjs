// Local preflight only: read public config and sync the two CSP origins.
import { readFile, writeFile } from "node:fs/promises";
import {
  SUPABASE_URL,
  SUPABASE_PUBLISHABLE_KEY,
  HOUSE_API_ORIGIN,
} from "../config.js";
function origin(value) {
  const u = new URL(value);
  if (
    u.protocol !== "https:" ||
    u.username ||
    u.password ||
    u.pathname !== "/" ||
    u.search ||
    u.hash ||
    value.includes("your-project")
  )
    throw new Error(
      "Use a complete HTTPS origin in config.js, with no path or placeholder.",
    );
  return u.origin;
}
if (
  !SUPABASE_PUBLISHABLE_KEY.startsWith("sb_publishable_") ||
  SUPABASE_PUBLISHABLE_KEY.includes("YOUR_")
)
  throw new Error(
    "Fill config.js with the Supabase publishable key. Never use a secret/service-role key.",
  );
const origins = [origin(SUPABASE_URL), origin(HOUSE_API_ORIGIN)];
const file = new URL("../vercel.json", import.meta.url),
  config = JSON.parse(await readFile(file, "utf8"));
const csp = config.headers[0].headers.find(
  (h) => h.key === "Content-Security-Policy",
);
csp.value = csp.value
  .replace(/connect-src[^;]+/, `connect-src 'self' ${origins.join(" ")}`)
  .replace(/img-src[^;]+/, `img-src 'self' ${origins.join(" ")}`);
await writeFile(file, JSON.stringify(config, null, 2) + "\n");
console.log(
  "Public config checked; vercel.json CSP matches config.js. No remote resources changed.",
);
