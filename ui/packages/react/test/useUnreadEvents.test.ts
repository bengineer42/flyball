// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { RigEvent, Severity } from "@flyball/client";
import { useUnreadEvents } from "../src/hooks/useUnreadEvents.js";
import { eventKey } from "../src/panels/EventsPanel.js";

let seq = 0;
function makeEvent(severity: Severity, overrides: Partial<RigEvent> = {}): RigEvent {
  seq += 1;
  return {
    time_ns: seq * 1_000_000_000,
    scope: "device",
    subject: `dev${seq}`,
    code: "changed",
    severity,
    message: `event ${seq}`,
    details: {},
    ...overrides,
  } as RigEvent;
}

describe("useUnreadEvents", () => {
  it("ignores events below minSeverity (default warning) for both unread and toasts", () => {
    const initial = [makeEvent("debug"), makeEvent("info")];
    const { result, rerender } = renderHook(({ events }) => useUnreadEvents(events), { initialProps: { events: initial } });

    expect(result.current.unread.size).toBe(0);
    expect(result.current.toasts).toHaveLength(0);

    const withMore = [...initial, makeEvent("info")];
    act(() => rerender({ events: withMore }));

    expect(result.current.unread.size).toBe(0);
    expect(result.current.toasts).toHaveLength(0);
  });

  it("includes events at/above minSeverity in unread", () => {
    const warn = makeEvent("warning");
    const err = makeEvent("error");
    const { result } = renderHook(({ events }) => useUnreadEvents(events), {
      initialProps: { events: [makeEvent("info"), warn, err] },
    });

    expect(result.current.unread.size).toBe(2);
    expect(result.current.unread.has(eventKey(warn))).toBe(true);
    expect(result.current.unread.has(eventKey(err))).toBe(true);
  });

  it("markRead / markAllRead remove from unread", () => {
    const warn = makeEvent("warning");
    const err = makeEvent("error");
    const { result } = renderHook(({ events }) => useUnreadEvents(events), {
      initialProps: { events: [warn, err] },
    });

    expect(result.current.unread.size).toBe(2);

    act(() => result.current.markRead(warn));
    expect(result.current.unread.size).toBe(1);
    expect(result.current.unread.has(eventKey(warn))).toBe(false);
    expect(result.current.unread.has(eventKey(err))).toBe(true);

    act(() => result.current.markAllRead());
    expect(result.current.unread.size).toBe(0);
  });

  it("toasts only the events that arrive after mount, never the initial backlog", () => {
    const backlog = [makeEvent("warning"), makeEvent("error")];
    const { result, rerender } = renderHook(({ events }) => useUnreadEvents(events), { initialProps: { events: backlog } });

    // The backlog present at mount must never be toasted.
    expect(result.current.toasts).toHaveLength(0);

    const fresh1 = makeEvent("warning");
    const fresh2 = makeEvent("info"); // below threshold: should not toast
    const fresh3 = makeEvent("error");
    const appended = [...backlog, fresh1, fresh2, fresh3];
    act(() => rerender({ events: appended }));

    expect(result.current.toasts.map((e) => eventKey(e))).toEqual([eventKey(fresh1), eventKey(fresh3)]);

    // Backlog keys still never appear.
    for (const e of backlog) {
      expect(result.current.toasts.some((t) => eventKey(t) === eventKey(e))).toBe(false);
    }
  });

  it("dismissToast marks the event read and removes it from the toast queue", () => {
    const initial = [makeEvent("info")];
    const { result, rerender } = renderHook(({ events }) => useUnreadEvents(events), { initialProps: { events: initial } });

    const fresh = makeEvent("error");
    const appended = [...initial, fresh];
    act(() => rerender({ events: appended }));

    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.unread.has(eventKey(fresh))).toBe(true);

    act(() => result.current.dismissToast(fresh));

    expect(result.current.toasts).toHaveLength(0);
    expect(result.current.unread.has(eventKey(fresh))).toBe(false);
  });

  it("does not dump the remaining list into toasts when the last-seen event falls off the front", () => {
    // Mount with a small backlog; the hook remembers the last event's key.
    const e1 = makeEvent("warning");
    const e2 = makeEvent("warning");
    const { result, rerender } = renderHook(({ events }) => useUnreadEvents(events), { initialProps: { events: [e1, e2] } });

    expect(result.current.toasts).toHaveLength(0);

    // Simulate useEvents trimming from the front: e1 and e2 both fall off,
    // replaced by a run of new events none of which is e2 (the last-seen key).
    const e3 = makeEvent("warning");
    const e4 = makeEvent("error");
    const e5 = makeEvent("warning");
    const trimmed = [e3, e4, e5]; // e1, e2 gone -- findIndex(lastKey) === -1

    expect(() => act(() => rerender({ events: trimmed }))).not.toThrow();

    // Must not dump the whole remaining list; only events from here on toast,
    // so this trimmed batch (arriving in one update, itself unseen) is treated
    // like a fresh backlog too and none of it should flood in as a burst.
    expect(result.current.toasts).toHaveLength(0);

    // But whatever arrives from now on toasts normally.
    const e6 = makeEvent("error");
    act(() => rerender({ events: [...trimmed, e6] }));
    expect(result.current.toasts.map((e) => eventKey(e))).toEqual([eventKey(e6)]);
  });
});
