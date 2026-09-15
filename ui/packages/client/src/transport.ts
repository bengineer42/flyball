/**
 * The two things a client needs from its environment: a way to make an HTTP
 * request and a way to open a stream. Everything above this file is written
 * against these interfaces, so a mock, a test harness or a non-browser
 * runtime supplies its own and nothing else changes.
 */

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface Request {
  method: Method;
  /** Path under the base URL, e.g. `/api/schema`. */
  path: string;
  query?: Record<string, string | number | boolean | null | undefined>;
  body?: unknown;
  signal?: AbortSignal;
}

export interface Response {
  status: number;
  /** Parsed JSON, or `undefined` for an empty body. */
  json: unknown;
}

/** A live stream: `close` ends it; the transport calls `onMessage` per parsed JSON message. */
export interface Subscription {
  close(): void;
}

export interface StreamHandlers<T = unknown> {
  onMessage(message: T): void;
  onOpen?(): void;
  onClose?(reason: "closed" | "error"): void;
}

export interface Transport {
  request(request: Request): Promise<Response>;
  /** `path` is under the base URL, e.g. `/ws/samples`. */
  stream(path: string, handlers: StreamHandlers): Subscription;
}

/** Thrown by the client when the server answers with an error status. */
export class RigError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly path: string,
  ) {
    super(`${status} ${path}: ${detail}`);
    this.name = "RigError";
  }
}

function buildUrl(base: string, path: string, query?: Request["query"]): string {
  const url = new URL(path, base);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }
  return url.toString();
}

/**
 * The browser transport: `fetch` for requests, `WebSocket` for streams, with
 * reconnection on drop (exponential backoff, capped). `base` is an absolute
 * origin, or `""` for same-origin, which is how the daemon serves the app.
 */
export function browserTransport(base: string = window.location.origin): Transport {
  return {
    async request({ method, path, query, body, signal }) {
      const init: RequestInit = { method, headers: {} };
      if (signal) init.signal = signal;
      if (body !== undefined) {
        init.body = JSON.stringify(body);
        (init.headers as Record<string, string>)["content-type"] = "application/json";
      }
      const response = await fetch(buildUrl(base, path, query), init);
      const text = await response.text();
      return { status: response.status, json: text ? JSON.parse(text) : undefined };
    },

    stream(path, handlers) {
      const url = buildUrl(base, path).replace(/^http/, "ws");
      let socket: WebSocket | null = null;
      let closed = false;
      let attempt = 0;
      let timer: ReturnType<typeof setTimeout> | null = null;

      const connect = () => {
        socket = new WebSocket(url);
        socket.onopen = () => {
          attempt = 0;
          handlers.onOpen?.();
        };
        socket.onmessage = (event) => handlers.onMessage(JSON.parse(event.data as string));
        socket.onclose = () => {
          handlers.onClose?.(closed ? "closed" : "error");
          if (!closed) {
            timer = setTimeout(connect, Math.min(30_000, 500 * 2 ** attempt++));
          }
        };
        socket.onerror = () => socket?.close();
      };
      connect();

      return {
        close() {
          closed = true;
          if (timer) clearTimeout(timer);
          socket?.close();
        },
      };
    },
  };
}
