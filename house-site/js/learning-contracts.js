const object = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid learning response");
  return value;
};
const count = (value) => {
  if (!Number.isInteger(value) || value < 0)
    throw new Error("Invalid learning count");
  return value;
};
export function learningFeed(value) {
  const data = object(value);
  count(data.personal_labels);
  count(data.house_truth);
  if (
    !Array.isArray(data.questions) ||
    !Array.isArray(data.runs) ||
    data.questions.length > 12
  )
    throw new Error("Invalid learning cards");
  for (const card of data.questions) {
    object(card);
    count(card.id);
    if (
      !["present", "kind", "missed", "same", "identity"].includes(card.task) ||
      typeof card.reason !== "string" ||
      typeof card.captured_at !== "string"
    )
      throw new Error("Invalid learning question");
    for (const src of [card.image_url, card.other_image_url].filter(Boolean)) {
      const url = new URL(src);
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
    }
    if (
      card.box !== null &&
      (!Array.isArray(card.box) ||
        card.box.length !== 4 ||
        !card.box.every((v) => Number.isFinite(v) && v >= 0 && v <= 1))
    )
      throw new Error("Invalid teaching box");
  }
  return data;
}
