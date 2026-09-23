// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useStartingRetry } from "../src/useStartingRetry.js";

describe("useStartingRetry", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("does nothing while inactive", () => {
    const onRetry = vi.fn();
    renderHook(() => useStartingRetry(false, onRetry));
    vi.advanceTimersByTime(10_000);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("retries on a backoff (1s, then 2s, then 4s, ...) while active", () => {
    // Cumulative fire times: 1000 (1s), 3000 (+2s), 7000 (+4s).
    const onRetry = vi.fn();
    renderHook(() => useStartingRetry(true, onRetry));

    vi.advanceTimersByTime(999);
    expect(onRetry).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1); // t=1000
    expect(onRetry).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(1999); // t=2999
    expect(onRetry).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1); // t=3000
    expect(onRetry).toHaveBeenCalledTimes(2);

    vi.advanceTimersByTime(3999); // t=6999
    expect(onRetry).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(1); // t=7000
    expect(onRetry).toHaveBeenCalledTimes(3);
  });

  it("caps the wait between retries, never growing unbounded", () => {
    const onRetry = vi.fn();
    renderHook(() => useStartingRetry(true, onRetry));
    // 1 + 2 + 4 + 8 + 8 + 8 = 31s should give six calls; the backoff caps at 8s, not 16s.
    vi.advanceTimersByTime(31_000);
    expect(onRetry).toHaveBeenCalledTimes(6);
  });

  it("stops when it goes inactive, and starts a fresh cycle if it becomes active again", () => {
    const onRetry = vi.fn();
    const { rerender } = renderHook(({ active }) => useStartingRetry(active, onRetry), { initialProps: { active: true } });

    vi.advanceTimersByTime(1000);
    expect(onRetry).toHaveBeenCalledTimes(1);

    rerender({ active: false });
    vi.advanceTimersByTime(10_000);
    expect(onRetry).toHaveBeenCalledTimes(1);

    rerender({ active: true });
    vi.advanceTimersByTime(999);
    expect(onRetry).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2);
    expect(onRetry).toHaveBeenCalledTimes(2);
  });
});
