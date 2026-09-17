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
  /** The body as text when it was not JSON (a proxy's HTML, a crashed handler's "Internal Server Error"). */
  text?: string;
}

/** A live stream: `close` ends it; the transport calls `onMessage` per parsed JSON message. */
export interface Subscription {
  close(): void;
}

export interface StreamHandlers<T = unknown> {
  onMessage(message: T): void;
  onOpen?(): void;
  /** `code` is the socket's close code where known: 4401 is the runner's "wrong or missing token". */
  onClose?(reason: "closed" | "error", code?: number): void;
}

export interface Transport {
  request(request: Request): Promise<Response>;
  /** Where requests go, when the transport has an origin: `""` for same-origin. A download link needs the URL, not a fetch. */
  readonly base?: string;
  /** The bearer token every request and stream carries, when the runner was given one to start with `--token`. */
  readonly token?: string;
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

/**
 * Where the page was served from, without the file: `https://host` at the root,
 * `https://host/flyball/humidity` under a sub-path. The default base, so a
 * runner started with `--root-path` behind the same prefix is found without
 * telling the app anything.
 */
export function pageBase(): string {
  return new URL(".", window.location.href).href.replace(/\/$/, "");
}

function buildUrl(base: string, path: string, query?: Request["query"]): string {
  // Concatenate rather than resolve: `new URL(path, base)` drops the base's own path.
  const url = new URL(base + path, window.location.href);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }
  return url.toString();
}

/**
 * The browser transport: `fetch` for requests, `WebSocket` for streams, with
 * reconnection on drop (exponential backoff, capped). `base` is an absolute
 * origin, with the runner's `--root-path` if it has one; default: where the
 * page itself was served from (`pageBase`).
 * `token`: a runner started with `--token` refuses everything without it --
 * a header on a request, `?token=` on a socket (a browser cannot set headers
 * on one). A socket the runner closes for a wrong or missing token (4401) is
 * not retried: nothing about reconnecting would fix it.
 */
export function browserTransport(base: string = pageBase(), token?: string): Transport {
  return {
    base,
    token,
    async request({ method, path, query, body, signal }) {
      const init: RequestInit = { method, headers: {} };
      if (signal) init.signal = signal;
      if (token) (init.headers as Record<string, string>).authorization = `Bearer ${token}`;
      if (body !== undefined) {
        init.body = JSON.stringify(body);
        (init.headers as Record<string, string>)["content-type"] = "application/json";
      }
      const response = await fetch(buildUrl(base, path, query), init);
      const text = await response.text();
      if (!text) return { status: response.status, json: undefined };
      try {
        return { status: response.status, json: JSON.parse(text) };
      } catch (e) {
        // Not JSON: an error page, or a handler that died before serialising. Hand the text up rather than a parse error.
        if (response.status >= 400) return { status: response.status, json: undefined, text };
        throw e;
      }
    },

    stream(path, handlers) {
      const url = buildUrl(base, path, token ? { token } : undefined).replace(/^http/, "ws");
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
        socket.onclose = (event) => {
          handlers.onClose?.(closed ? "closed" : "error", event.code);
          if (!closed && event.code !== 4401) {
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
