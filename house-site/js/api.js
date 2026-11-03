import { learningFeed } from "./learning-contracts.js";
import { HOUSE_API_ORIGIN } from "../config.js";
import { accessToken, mock } from "./auth.js";
import * as parse from "./contracts.js";
const origin = mock ? location.origin : HOUSE_API_ORIGIN;
export class HouseError extends Error {
  /** @param {string} message @param {number} status */
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}
/** @param {string} path @param {{public?:boolean, body?:unknown}} [options] @returns {Promise<unknown>} */
async function request(path, options = {}) {
  const headers = new Headers({ Accept: "application/json" });
  if (!options.public) {
    const token = await accessToken();
    if (!token) throw new HouseError("Please sign in again.", 401);
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (options.body !== undefined)
    headers.set("Content-Type", "application/json");
  let response;
  try {
    response = await fetch(`${origin}/house/api${path}`, {
      method: options.body === undefined ? "GET" : "POST",
      headers,
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    throw new HouseError("Dish Watch is offline right now", 0);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      data &&
      typeof data === "object" &&
      "detail" in data &&
      typeof data.detail === "string"
        ? data.detail
        : "That didn’t go through. Please try again.";
    throw new HouseError(
      response.status >= 500 ? "Dish Watch is offline right now" : detail,
      response.status,
    );
  }
  return data;
}
/** @template T @param {Promise<unknown>} response @param {(value:unknown)=>T} parser */
async function parsed(response, parser) {
  const value = await response;
  try {
    return parser(value);
  } catch {
    throw new HouseError("Dish Watch is offline right now", 502);
  }
}
export const getMe = () => parsed(request("/me"), parse.me);
/** @param {string} [start] */
export const getWeek = (start = "") =>
  parsed(
    request(`/week${start ? `?start=${encodeURIComponent(start)}` : ""}`),
    parse.week,
  );
/** @param {string} token @param {string} password */
export const redeem = (token, password) =>
  parsed(
    request("/join", { public: true, body: { token, password } }),
    parse.join,
  );
/** @param {string|number} id @param {'name'|'dispute'|'settle'|'verdict'} action @param {unknown} body */
export const act = (id, action, body) =>
  parsed(
    request(`/visits/${encodeURIComponent(id)}/${action}`, { body }),
    parse.visit,
  );

/** Teaching responses are isolated from the league payload. */
export const learningRequest = async (path, body) => { const data = await request(path, body === undefined ? {} : { body }); if(path !== "/learning") return data; try { return learningFeed(data); } catch { throw new HouseError("Dish Watch is offline right now", 502); } };
