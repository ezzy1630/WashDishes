// Parse untrusted teaching responses before they reach the views.
/** @typedef {'present'|'kind'|'missed'|'same'|'identity'} LearningTask */
/** @typedef {[number, number, number, number]} LearningBox */
/** @param {unknown} value @returns {Record<string, unknown>} */
function object(value) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid learning response");
  return /** @type {Record<string, unknown>} */ (value);
}
/** @param {unknown} value */
function count(value) {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0)
    throw new Error("Invalid learning count");
  return value;
}
/** @param {unknown} value */
function string(value) {
  if (typeof value !== "string") throw new Error("Invalid learning text");
  return value;
}
/** @param {unknown} value @returns {LearningTask} */
function task(value) {
  if (
    value === "present" ||
    value === "kind" ||
    value === "missed" ||
    value === "same" ||
    value === "identity"
  )
    return value;
  throw new Error("Invalid learning task");
}
/** @param {unknown} value */
function photo(value) {
  const src = string(value),
    url = new URL(src);
  if (
    url.username ||
    url.password ||
    !(
      url.protocol === "https:" ||
      (url.protocol === "http:" &&
        ["127.0.0.1", "localhost"].includes(url.hostname))
    )
  )
    throw new Error("Invalid teaching photo");
  return src;
}
/** @param {unknown} value @returns {LearningBox|null} */
function box(value) {
  if (value === null) return null;
  if (
    !Array.isArray(value) ||
    value.length !== 4 ||
    !value.every(
      (v) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1,
    )
  )
    throw new Error("Invalid teaching box");
  return [value[0], value[1], value[2], value[3]];
}
/** @param {unknown} value */
function question(value) {
  const card = object(value);
  return {
    id: count(card.id),
    task: task(card.task),
    reason: string(card.reason),
    captured_at: string(card.captured_at),
    image_url: photo(card.image_url),
    other_image_url:
      card.other_image_url == null ? null : photo(card.other_image_url),
    box: box(card.box),
    imageMissing: false,
  };
}
/** @param {unknown} value */
function accuracy(value) {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > 1
  )
    throw new Error("Invalid learning accuracy");
  return value;
}
/** @param {unknown} value */
function metric(value) {
  const m = object(value);
  return {
    model: string(m.model),
    before_accuracy: accuracy(m.before_accuracy),
    after_accuracy: accuracy(m.after_accuracy),
  };
}
/** @param {unknown} value */
export function learningFeed(value) {
  const data = object(value);
  if (
    !Array.isArray(data.questions) ||
    !Array.isArray(data.runs) ||
    data.questions.length > 12 ||
    (data.metrics !== undefined && !Array.isArray(data.metrics))
  )
    throw new Error("Invalid learning cards");
  return {
    personal_labels: count(data.personal_labels),
    house_truth: count(data.house_truth),
    last_learned: data.last_learned == null ? null : string(data.last_learned),
    questions: data.questions.map(question),
    metrics: (data.metrics || []).map(metric),
  };
}
/** @param {unknown} value */
export function learningAnswer(value) {
  const data = object(value),
    state = data.state;
  if (state !== "trusted" && state !== "conflict" && state !== "pending")
    throw new Error("Invalid teaching receipt");
  return { id: count(data.id), state };
}
/** @param {unknown} value */
export function learningFeedback(value) {
  const data = object(value);
  if (typeof data.independent_review !== "boolean")
    throw new Error("Invalid teaching feedback");
  return {
    question_id: count(data.question_id),
    independent_review: data.independent_review,
  };
}
