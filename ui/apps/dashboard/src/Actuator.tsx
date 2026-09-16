import { ActuatorPanel, useCommands, useDeviceView, useLoops } from "@flyball/react";
import type { ActuatorSchema, DeviceState } from "@flyball/client";
import { Form as MuiForm } from "@rjsf/mui";

/** The wiring: a pure `ActuatorPanel` plus the hooks that fetch its view and run its commands. */
export function Actuator({
  schema,
  state,
  bare,
  omitFields,
}: {
  schema: ActuatorSchema;
  state: DeviceState | undefined;
  /** No outer card/header: for the Controllers page's collapsible below a loop's own faceplate card. */
  bare?: boolean;
  /** State fields to leave out (e.g. `demand`/`output_range` when a loop's Output row above already shows them). */
  omitFields?: string[];
}) {
  const view = useDeviceView("actuators", schema.name);
  const commands = useCommands("actuators", schema.name);
  // A loop is named for the actuator it drives; borrow its channel's precision so the demand
  // and output range read the same number of decimals here as on the Controllers page's faceplate.
  const { loops } = useLoops();
  const precision = loops[schema.name]?.channel.precision ?? undefined;
  return (
    <ActuatorPanel
      schema={schema}
      state={state}
      view={view.data}
      precision={precision}
      form={MuiForm}
      bare={bare}
      omitFields={omitFields}
      onRun={(tag, args) =>
        commands
          .run(tag, args)
          .then((r) => {
            view.refresh(); // settings may have changed
            return r;
          })
          .catch(() => undefined)
      }
      busy={commands.busy}
      results={commands.results}
    />
  );
}
