import {
  learningFeed,
  learningAnswer,
  learningFeedback,
} from "./learning-contracts.js";
import { HOUSE_API_ORIGIN } from "../config.js";
import { accessToken, mock } from "./auth.js";
import * as parse from "./contracts.js";
const origin = mock ? location.origin : HOUSE_API_ORIGIN;
const offlineMessage = "Can’t reach the kitchen right now.";
export class HouseError extends Error {
  /** @param {string} message @param {number} status */
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}
/** @param {string} path @param {{public?:boolean, body?:unknown, signal?:AbortSignal}} [options] @returns {Promise<unknown>} */
async function request(path, options = {}) {
  // A cold sign-in check or a busy moment on the kitchen computer usually clears
  // in a second or two; reads ride that out instead of showing a false outage.
  // Writes never repeat here: the caller decides whether one is safe to resend.
  const started = Date.now();
  for (let attempt = 0; ; attempt++) {
    try {
      return await requestOnce(path, options);
    } catch (error) {
      const transient =
        error instanceof HouseError &&
        (error.status === 0 || error.status === 429 || error.status >= 500);
      if (
        !transient ||
        options.body !== undefined ||
        options.signal?.aborted ||
        attempt >= 2 ||
        Date.now() - started > 8000
      )
        throw error;
      await new Promise((resolve) => setTimeout(resolve, 500 + attempt * 1000));
    }
  }
}
/** @param {string} path @param {{public?:boolean, body?:unknown, signal?:AbortSignal}} [options] @returns {Promise<unknown>} */
async function requestOnce(path, options = {}) {
  const headers = new Headers({ Accept: "application/json" });
  if (!options.public) {
    const token = await accessToken();
    if (!token) throw new HouseError("Please sign in again.", 401);
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (options.body !== undefined)
    headers.set("Content-Type", "application/json");
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timeout = setTimeout(abort, 15000);
  if (options.signal?.aborted) abort();
  else options.signal?.addEventListener("abort", abort, { once: true });
  let response, data;
  try {
    response = await fetch(`${origin}/house/api${path}`, {
      method: options.body === undefined ? "GET" : "POST",
      headers,
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      keepalive: options.body !== undefined,
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal: controller.signal,
    });
    data = await response.json().catch(() => null);
  } catch {
    throw new HouseError(offlineMessage, 0);
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abort);
  }
  if (!response.ok) {
    const detail =
      data &&
      typeof data === "object" &&
      "detail" in data &&
      typeof data.detail === "string"
        ? data.detail
        : "That didn’t go through. Please try again.";
    throw new HouseError(
      response.status >= 500 ? offlineMessage : detail,
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
    throw new HouseError("Something went wrong on our end. Try again.", 502);
  }
}
export const getMe = () => parsed(request("/me"), parse.me);
export const getFootage = () => parsed(request("/footage"), parse.footage);
/** @param {number} at @param {AbortSignal} [signal] */
export const getFootageChunk = (at, signal) =>
  parsed(request(`/footage/chunk?at=${encodeURIComponent(at)}`, { signal }), parse.footageChunk);
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
export const getLearning = () => parsed(request("/learning"), learningFeed);
/** @param {number} id @param {string} answer @param {{box: import('./learning-contracts.js').LearningBox, kind:string}|null} value */
export const answerLearning = (id, answer, value) =>
  parsed(
    request(`/learning/${id}/answer`, { body: { answer, value } }),
    learningAnswer,
  );
/** @param {number} id */
export const undoLearning = (id) =>
  request(`/learning/labels/${id}/undo`, { body: {} });
/** @param {string|number} id */
export const feedbackLearning = (id) =>
  parsed(
    request(`/visits/${encodeURIComponent(id)}/feedback`, {
      body: { task: "missed" },
    }),
    learningFeedback,
  );
