import type { Conversation } from "../api/types";
import { inboxCounts } from "../layout/dashboard";

function conv(overrides: Partial<Conversation>): Conversation {
  return {
    id: Math.random().toString(),
    status: "open",
    mode: "bot",
    waiting_reply: false,
    assigned: null,
    last_message_at: null,
    ...overrides,
  };
}

test("cuenta la bandeja para Inicio", () => {
  const counts = inboxCounts(
    [
      conv({ waiting_reply: true }),
      conv({ mode: "human", assigned: { user_id: "yo", name: "Andre" } }),
      conv({ mode: "human", waiting_reply: true, assigned: { user_id: "otra", name: "Ana" } }),
    ],
    "yo",
  );
  expect(counts).toEqual({ open: 3, waiting: 2, human: 2, mine: 1, capped: false });
});
