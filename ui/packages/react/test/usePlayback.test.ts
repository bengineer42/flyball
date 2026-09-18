// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { Request, Response, StreamHandlers, Subscription, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { usePlayback, PLAYBACK_STEP_S } from "../src/hooks/usePlayback.js";

/** A transport whose only route this hook needs (`GET /api/recording`) returns a fixed session;
 * `stream` never delivers anything, so `nowS` always falls back to the (fake) wall clock. */
function fakeTransport(sessionStartS: number): Transport {
  return {
    async request({ path }: Request): Promise<Response> {
      if (path === "/api/recording") {
        return { status: 200, json: { id: 7, start_ns: Math.round(sessionStartS * 1e9), end_ns: null, kind: "scratch", pinned: false } };
      }
      return { status: 404, json: undefined };
    },
    stream(_path: string, _handlers: StreamHandlers): Subscription {
      return { close: () => undefined };
    },
  };
}

/** Fake timers freeze `setTimeout`, so `waitFor`'s own polling never fires; flush the
 * `/api/recording` fetch's microtask queue directly instead. */
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function renderPlayback(sessionStartS: number) {
  const transport = fakeTransport(sessionStartS);
  const rendered = renderHook(() => usePlayback(), {
    wrapper: ({ children }) => createElement(RigProvider, { transport }, children),
  });
  await flush();
  return rendered;
}

describe("usePlayback", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(1_000_000 * 1000)); // an arbitrary fixed "now"
  });
  afterEach(() => vi.useRealTimers());

  it("starts live: not paused, atS tracks nowS, sessionId/startS come from /api/recording", async () => {
    const { result } = await renderPlayback(1_000_000 - 3600); // session started an hour ago
    expect(result.current.paused).toBe(false);
    expect(result.current.startS).toBeCloseTo(1_000_000 - 3600, 0);
    expect(result.current.atS).toBeCloseTo(result.current.nowS, 0);
  });

  it("rewind pauses and moves atS back by PLAYBACK_STEP_S", async () => {
    const { result } = await renderPlayback(1_000_000 - 3600);
    const before = result.current.nowS;
    act(() => result.current.rewind());
    expect(result.current.paused).toBe(true);
    expect(result.current.atS).toBeCloseTo(before - PLAYBACK_STEP_S, 0);
  });

  it("rewind clamps at the session's start, does not go earlier", async () => {
    const { result } = await renderPlayback(1_000_000 - 10); // only 10s of history
    act(() => result.current.rewind());
    expect(result.current.atS).toBeCloseTo(1_000_000 - 10, 0);
  });

  it("fastForward past nowS resumes to live rather than overshooting", async () => {
    const { result } = await renderPlayback(1_000_000 - 3600);
    act(() => result.current.rewind());
    expect(result.current.paused).toBe(true);
    act(() => result.current.fastForward(100_000)); // far more than the gap back to live
    expect(result.current.paused).toBe(false);
    expect(result.current.atS).toBeCloseTo(result.current.nowS, 0);
  });

  it("resume goes straight back to live from anywhere", async () => {
    const { result } = await renderPlayback(1_000_000 - 3600);
    act(() => result.current.seek(1_000_000 - 1800));
    expect(result.current.paused).toBe(true);
    act(() => result.current.resume());
    expect(result.current.paused).toBe(false);
    expect(result.current.atS).toBeCloseTo(result.current.nowS, 0);
  });

  it("pause freezes exactly where playback is, without moving it", async () => {
    const { result } = await renderPlayback(1_000_000 - 3600);
    const before = result.current.nowS;
    act(() => result.current.pause());
    expect(result.current.paused).toBe(true);
    expect(result.current.atS).toBeCloseTo(before, 0);
  });

  it("seek below the session's start clamps up to it", async () => {
    const { result } = await renderPlayback(1_000_000 - 3600);
    act(() => result.current.seek(0));
    expect(result.current.atS).toBeCloseTo(1_000_000 - 3600, 0);
  });
});
