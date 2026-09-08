import assert from "node:assert/strict";
import test from "node:test";

import { pendingPdfTurn } from "../src/renderer/lib/pdfTurns.ts";

test("an ambiguous PDF retry reuses its request ID without binding changed text", () => {
  let created = 0;
  const create = () => `request-${++created}`;
  const first = pendingPdfTurn(null, "Summarize this PDF", create);
  const retry = pendingPdfTurn(first, "Summarize this PDF", create);
  const changed = pendingPdfTurn(first, "Summarize only page two", create);

  assert.strictEqual(retry, first);
  assert.equal(changed.requestId, "request-2");
  assert.equal(changed.message, "Summarize only page two");
});
