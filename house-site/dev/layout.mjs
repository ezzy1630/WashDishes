// Observable geometry: document width plus visible content inside its own card.
// Native horizontal scrollers (week chooser and enlarged photos) are intentional.
export async function inspectLayout(page) {
  return page.evaluate(() => {
    const root = document.scrollingElement;
    const violations = [];
    const cards =
      ".auth,.duty,.pick,.standings,.rankings li,.name-cta,.house-rules,.name-panel,.dish-record,.reviewed-visit,.week-choice,.past-detail,.offline,.duplicate-choice,.photo,.arrival-photo,.naming,.learning-card,.learning-panel,.personal-status,.learning-prompt";
    const describe = (el) =>
      `${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.trim().replace(/\s+/g, ".") : ""}`;
    for (const el of document.querySelectorAll("main *, nav *")) {
      if (!(el instanceof Element) || el.closest("details:not([open])"))
        continue;
      const rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height) continue;
      const card = el.parentElement?.closest(cards);
      if (card && card !== el) {
        const bounds = card.getBoundingClientRect();
        let scroll = el.parentElement,
          intentional = false;
        while (scroll && scroll !== card) {
          if (["auto", "scroll"].includes(getComputedStyle(scroll).overflowX)) {
            intentional = true;
            break;
          }
          scroll = scroll.parentElement;
        }
        if (
          !intentional &&
          (rect.left < bounds.left - 0.75 ||
            rect.right > bounds.right + 0.75 ||
            rect.top < bounds.top - 0.75 ||
            rect.bottom > bounds.bottom + 0.75)
        )
          violations.push({
            element: describe(el),
            card: describe(card),
            kind: "box outside card",
            left: rect.left,
            right: rect.right,
            cardLeft: bounds.left,
            cardRight: bounds.right,
          });
      }
      // Ranges catch clipped glyphs even when the element box fits the document.
      for (const node of el.childNodes) {
        if (node.nodeType !== Node.TEXT_NODE || !node.textContent.trim())
          continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const text of range.getClientRects()) {
          if (text.left < rect.left - 0.75 || text.right > rect.right + 0.75)
            violations.push({
              element: describe(el),
              kind: "text outside element",
              text: node.textContent.trim().slice(0, 70),
              left: text.left,
              right: text.right,
              boxLeft: rect.left,
              boxRight: rect.right,
            });
        }
      }
    }
    return {
      scrollWidth: root.scrollWidth,
      clientWidth: root.clientWidth,
      violations,
    };
  });
}
