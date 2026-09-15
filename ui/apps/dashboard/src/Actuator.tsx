import { ActuatorPanel, useCommands, useDeviceView } from "@flyball/react";
import type { ActuatorSchema, DeviceState } from "@flyball/client";
import { Form as MuiForm } from "@rjsf/mui";

/** The wiring: a pure `ActuatorPanel` plus the hooks that fetch its view and run its commands. */
export function Actuator({ schema, state }: { schema: ActuatorSchema; state: DeviceState | undefined }) {
  const view = useDeviceView("actuators", schema.name);
  const commands = useCommands("actuators", schema.name);
  return (
    <ActuatorPanel
      schema={schema}
      state={state}
      view={view.data}
      form={MuiForm}
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
