// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { browserTransport } from "@flyball/client";

/** Captures the URL a `WebSocket` was constructed with; never actually connects. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  close() {
    /* no-op */
  }
}

const realWebSocket = globalThis.WebSocket;

beforeEach(() => {
  FakeWebSocket.instances = [];
  // @ts-expect-error -- a minimal stand-in, not the real constructor's shape
  globalThis.WebSocket = FakeWebSocket;
});

afterEach(() => {
  globalThis.WebSocket = realWebSocket;
  vi.restoreAllMocks();
});

describe("browserTransport().stream never puts a credential in the socket URL", () => {
  it("carries no ?token=, even when the transport was given one (a browser cannot set a header on a socket anyway)", () => {
    const transport = browserTransport("http://localhost:8000", "shh-secret-token");
    const sub = transport.stream("/ws/samples", { onMessage: () => undefined });
    expect(FakeWebSocket.instances).toHaveLength(1);
    const url = FakeWebSocket.instances[0]!.url;
    expect(url).toBe("ws://localhost:8000/ws/samples");
    expect(url).not.toContain("token");
    sub.close();
  });
});
