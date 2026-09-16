import { Alert } from "@mui/material";
import type { RigSchema } from "@flyball/client";
import { useReaderRuns } from "../model.js";
import { ReaderCard, SectionHead, StateBlock } from "../cards.js";
import { SourceIcon } from "../icons.js";
import { hashFor } from "../router.js";
import { Crumbs } from "./Sources.js";
import { PageBar } from "../PageBar.js";

// The actuator detail page moved into `pages/Controllers.tsx` (one card per actuator, looped or
// not) when the Loops and Actuators pages merged; `#/actuators/<name>` now redirects there.

/** One reader: run state and its sources as links. */
export function ReaderDetail({ schema, name }: { schema: RigSchema; name: string }) {
  const runs = useReaderRuns();
  const reader = schema.readers[name];
  if (!reader) return <Alert severity="warning">No such reader.</Alert>;
  return (
    <>
      <PageBar>
        <Crumbs items={[{ label: "overview", href: hashFor("overview") }, { label: "readers" }, { label: reader.label ?? name }]} />
      </PageBar>
      <div className="grid">
        <ReaderCard className="c6" reader={reader} run={runs[name]} link={false} />
      </div>
    </>
  );
}

/** Every reader, for `#/readers` without a name. */
export function Readers({ schema }: { schema: RigSchema }) {
  const runs = useReaderRuns();
  const readers = Object.values(schema.readers);
  return (
    <>
      <SectionHead icon={SourceIcon} title="Readers" count={readers.length} />
      {readers.length === 0 ? (
        <StateBlock state="empty" message="No readers declared. Add one to the rig file to see it here." />
      ) : (
        <div className="grid">
          {readers.map((r) => (
            <ReaderCard key={r.name} className="c3" reader={r} run={runs[r.name]} />
          ))}
        </div>
      )}
    </>
  );
}
