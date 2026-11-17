import type { Conversation } from "../api/types";
import { inboxCounts } from "../layout/dashboard";

function conv(overrides: Partial<Conversation>): Conversation {
  return {
    id: Math.random().toString(),
    status: "open",
    mode: "bot",
    contact: { id: "k1", name: "María", phone: "+584121234567" },
    number_id: "n1",
    number_phone: null,
    waiting_reply: false,
    assigned: null,
    last_message: null,
    last_message_at: null,
    created_at: "2026-11-16T12:00:00Z",
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
