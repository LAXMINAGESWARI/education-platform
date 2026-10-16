const ITEM_FRAME =
  /^Item\s+\d+\s+\([^)]+\)\s+for\s+[^:]+:\s*which statement is grounded in this source excerpt:\s*/i;
const FACT_FRAME = /^A fact from the .+? excerpt:\s*/i;
const CONTRADICTS = /^An unrelated claim that contradicts .+\.$/i;
const MIXUP = /^A common mix-up about .+\.$/i;
const UNSUPPORTED = /^A detail that the .+ excerpt does not support\.$/i;
const SECTION_LEAD =
  /^(?:what have we discussed\??|exercise\s+\d+(?:\.\d+)?|\d+\.\d+)\s*/i;
const TITLE_BEFORE_MATH = /^(?:[A-Z][A-Za-z]+(?:\s+[A-Za-z]+){0,10})\s+(?=\$\$)/;

/** Student-facing quiz copy. Drops source headings and filler that names where the item came from. */
export function presentQuizText(text: string): string {
  const cleaned = text.replace(/[\uE000-\uF8FF]/g, "").replace(/\s*\[Diagram\]\s*/gi, " ").replace(/\s+/g, " ").trim();
  if (ITEM_FRAME.test(cleaned)) {
    return "Which statement is correct?";
  }
  if (CONTRADICTS.test(cleaned)) {
    return "This reverses the correct steps.";
  }
  if (MIXUP.test(cleaned)) {
    return "This changes the equation in a way that is not allowed.";
  }
  if (UNSUPPORTED.test(cleaned)) {
    return "This answer does not follow from the given equation.";
  }
  let body = cleaned.replace(FACT_FRAME, "");
  for (let pass = 0; pass < 3; pass += 1) {
    const next = body.replace(SECTION_LEAD, "").replace(TITLE_BEFORE_MATH, "");
    if (next === body) break;
    body = next;
  }
  return dropUnclosedMath(body.trim());
}

/** Stored excerpts are sometimes cut off mid-equation. Drop the unfinished tail. */
function dropUnclosedMath(text: string): string {
  let out = text;
  if (out.split("$$").length % 2 === 0) {
    out = out.split("$$").slice(0, -1).join("$$").trim();
  }
  if (out.split("$").length % 2 === 0) {
    out = out.split("$").slice(0, -1).join("$").trim();
  }
  return out.replace(/\\[a-zA-Z]*\{[^}]*$/, "").trim();
}
