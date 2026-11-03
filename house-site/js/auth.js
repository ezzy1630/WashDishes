import { createClient } from "../vendor/supabase-js-2.118.0.js";
import { SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY } from "../config.js";

// The deploy excludes /dev. A query string alone can never turn auth off.
const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(
  location.hostname,
);
export const mock =
  loopback && new URLSearchParams(location.search).get("mock") === "1";
export const configured = (() => {
  try {
    const url = new URL(SUPABASE_URL);
    return (
      url.protocol === "https:" &&
      !url.username &&
      !url.password &&
      !SUPABASE_URL.includes("your-project") &&
      SUPABASE_PUBLISHABLE_KEY.startsWith("sb_publishable_")
    );
  } catch {
    return false;
  }
})();
const client =
  !mock && configured
    ? createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: false,
          storageKey: "highest-house-auth",
        },
      })
    : null;

/** @returns {Promise<string | null>} */
export async function accessToken() {
  if (mock) {
    const slug = sessionStorage.getItem("dw-mock-user");
    return slug ? `mock:${slug}` : null;
  }
  if (!client) return null;
  const { data, error } = await client.auth.getSession();
  if (error)
    throw new Error("Your sign-in needs refreshing. Please sign in again.");
  return data.session?.access_token || null;
}
/** @param {string} slug @param {string} password */
export async function signIn(slug, password) {
  if (mock) {
    const r = await fetch("/dev/sign-in", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, password }),
    });
    await r.json();
    if (!r.ok)
      throw new Error("That name and password didn’t match. Try again.");
    sessionStorage.setItem("dw-mock-user", slug);
    return;
  }
  if (!client)
    throw new Error(
      "Sign-in isn’t set up yet. Ask Ezzy to finish connecting the house.",
    );
  const { error } = await client.auth.signInWithPassword({
    email: `${slug}@highest-house.ezzyrappeport.com`,
    password,
  });
  if (error)
    throw new Error(
      error.status === 400
        ? "That name and password didn’t match. Try again."
        : "Sign-in isn’t available right now. Please try again.",
    );
}
export async function signOut() {
  if (mock) sessionStorage.removeItem("dw-mock-user");
  else if (client) {
    const { error } = await client.auth.signOut({ scope: "local" });
    if (error) throw new Error("Couldn’t sign out. Please try again.");
  }
}
/** @param {() => void} callback */
export function onSignedOut(callback) {
  client?.auth.onAuthStateChange((event) => {
    if (event === "SIGNED_OUT") callback();
  });
}
