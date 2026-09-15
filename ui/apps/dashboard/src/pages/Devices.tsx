import { Alert, Link, Paper, Typography } from "@mui/material";
import { useLoops } from "@flyball/react";
import type { DeviceState, RigSchema } from "@flyball/client";
import { useReaderRuns } from "../model.js";
import { Actuator } from "../Actuator.js";
import { ReaderCard } from "../cards.js";
import { hashFor, hrefFor } from "../router.js";
import { Crumbs } from "./Sources.js";

/** One actuator's panel, and the loop that drives it (a loop is named after its actuator). */
export function ActuatorDetail({ schema, name, states }: { schema: RigSchema; name: string; states: Record<string, DeviceState> }) {
  const { loops } = useLoops(60);
  const actuator = schema.actuators[name];
  if (!actuator) return <Alert severity="warning">No such actuator.</Alert>;
  const loop = loops[name];
  return (
    <>
      <Crumbs items={[{ label: "actuators", href: hashFor("actuators") }, { label: name }]} />
      <Paper sx={{ p: 1.5, mb: 1.5 }}>
        <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 0.5 }}>
          Driven by
        </Typography>
        {loop ? (
          <Typography>
            <Link href={hrefFor({ kind: "loop", name: loop.name })} underline="hover">
              loop {loop.name}
            </Link>{" "}
            <Typography component="span" variant="body2" color="text.secondary">
              {loop.mode} · on{" "}
              <Link href={hrefFor({ kind: "channel", name: loop.channel.source, measurand: loop.channel.measurand })} underline="hover">
                {loop.channel.source}.{loop.channel.measurand}
              </Link>
            </Typography>
          </Typography>
        ) : (
          <Typography variant="body2" color="text.secondary">
            no loop; commands only
          </Typography>
        )}
      </Paper>
      <Actuator schema={actuator} state={states[name]} />
    </>
  );
}

/** One reader: run state and its sources as links. */
export function ReaderDetail({ schema, name }: { schema: RigSchema; name: string }) {
  const runs = useReaderRuns();
  const reader = schema.readers[name];
  if (!reader) return <Alert severity="warning">No such reader.</Alert>;
  return (
    <>
      <Crumbs items={[{ label: "overview", href: hashFor("overview") }, { label: "readers" }, { label: name }]} />
      <div className="tiles tiles-wide">
        <ReaderCard reader={reader} run={runs[name]} link={false} />
      </div>
    </>
  );
}

/** Every reader, for `#/readers` without a name. */
export function Readers({ schema }: { schema: RigSchema }) {
  const runs = useReaderRuns();
  const readers = Object.values(schema.readers);
  if (readers.length === 0) return <Typography color="text.secondary">no readers attached</Typography>;
  return (
    <div className="tiles tiles-wide">
      {readers.map((r) => (
        <ReaderCard key={r.name} reader={r} run={runs[r.name]} />
      ))}
    </div>
  );
}
