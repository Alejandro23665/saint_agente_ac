import { parseSse } from "../realtime/events";

test("parte los avisos y deja lo incompleto para después", () => {
  const text =
    'event: ready\ndata: {"type": "ready"}\n\n' +
    ": ping\n\n" +
    'event: conversation.changed\ndata: {"type": "conversation.changed", "conversation_id": "c1"}\n\n' +
    'event: inbox.changed\ndata: {"type": "inb';
  const { events, rest } = parseSse(text);
  expect(events).toEqual([
    { type: "ready" },
    { type: "conversation.changed", conversation_id: "c1" },
  ]);
  expect(rest).toBe('event: inbox.changed\ndata: {"type": "inb');
  expect(parseSse(rest + 'ox.changed"}\n\n').events).toEqual([{ type: "inbox.changed" }]);
});

test("ignora un aviso mal formado", () => {
  expect(parseSse("data: {roto\n\n").events).toEqual([]);
});
