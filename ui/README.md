# flyball UI

A library first, an app second. Nothing here knows what a rig measures: every
panel is a function of the JSON the daemon publishes (see the book's *HTTP and
websocket API* and *Wire format* pages), and the app is the smallest consumer
of the library.

```
packages/client   @flyball/client   wire types, Transport interface, RigClient, schema helpers. No framework.
packages/react    @flyball/react    RigProvider + hooks (the only place fetching happens), SchemaForm, panels.
apps/dashboard    @flyball/dashboard  the served app: provider, rig document, one panel per actuator.
```

## Run

```bash
cd examples/humidity && uv run humidity-sim        # a rig with nothing plugged in, on :8000
cd ui && npm install && npm run dev                # Vite on :5173, /api and /ws proxied to :8000
```

`FLYBALL_URL=http://pi:8000 npm run dev` points the proxy elsewhere.

## Use as a library

```tsx
import { RigProvider, useRigSchema, useActuatorStates, useCommands, ActuatorPanel } from "@flyball/react";
import "@flyball/react/styles.css";

function Pumps() {
  const schema = useRigSchema();                       // GET /api/schema
  const { states } = useActuatorStates();              // /ws/actuators
  const commands = useCommands("actuators", "pumps");  // POST /api/actuators/pumps/{command}
  const pumps = schema.data?.actuators.pumps;
  return pumps ? <ActuatorPanel schema={pumps} state={states.pumps} onRun={commands.run} busy={commands.busy} results={commands.results} /> : null;
}

<RigProvider url="http://pi:8000"><Pumps /></RigProvider>
```

The rules that keep it embeddable:

- **Panels take data and emit events.** `ActuatorPanel`, `CommandForm`, `StateView`, `SchemaForm` never fetch; they render props and call callbacks. They work against a mock, a recording, or someone else's server.
- **Hooks fetch.** `useRigSchema`, `useDeviceView`, `useActuatorStates`, `useStream`, `useCommands`. Each returns the same `{data, error, loading, refresh}` shape, so they can be swapped for TanStack Query later without touching a panel.
- **Transport is an interface.** `RigProvider` takes a `transport` prop; `browserTransport` (fetch + WebSocket with reconnect) is the default. A test passes a fake.
- **No global state, no router, no leaking CSS.** Styles are scoped under `.fb-*` and driven by CSS variables (`--fb-accent`, …).

## Build

`npm run typecheck` (project references across all three), `npm run build` (packages emit `dist/` with `.d.ts`; the app emits `apps/dashboard/dist`). The `development` export condition resolves packages to source under Vite, so there is no build step in dev.

## Next

In the order of `UI.md` §6: graph panel (uPlot, `/ws/samples` + `/api/history/…/series`), the loop panel, layouts. The `/ws/samples` messages carry no `source` name yet — needed before a second source exists.
