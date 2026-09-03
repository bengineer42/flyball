/**
 * How this browser talks to the rig.
 *
 * Rig configuration proper — pump limits, gains, loop time — belongs to the
 * daemon's config file and is read from it, not edited here (R6 would change
 * that). What this page owns is the connection, the chart buffer, and the
 * simulated rig's behaviour.
 */

import type { Capabilities } from "../api/transport";
import type { Settings as SettingsValue } from "../state/settings";
import type { RigHandle } from "../state/useRig";
import { Badge, Card, NumberField, SelectField, TextField, Toggle } from "../components/primitives";

export interface SettingsProps {
  settings: SettingsValue;
  onChange(change: Partial<SettingsValue>): void;
  rig: RigHandle;
}

const FEATURES: ReadonlyArray<{ key: keyof Capabilities; label: string; note: string }> = [
  { key: "reachable", label: "Daemon reachable", note: "GET /api/health answers" },
  { key: "rigAttached", label: "Rig attached", note: "health reports a manager" },
  { key: "pumps", label: "Pumps", note: "/api/pumps" },
  { key: "controller", label: "Controller", note: "/api/controller" },
  { key: "recording", label: "Recording", note: "/api/recording/*" },
  { key: "suspend", label: "Suspend controller", note: "R3 — router not mounted" },
  { key: "programs", label: "Programs", note: "R1 — no endpoints yet" },
  { key: "history", label: "Stored history", note: "R4 — charts start empty on reload" },
];

export function Settings({ settings, onChange, rig }: SettingsProps) {
  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      <div className="stack">
        <Card title="Connection">
          <div className="stack tight">
            <SelectField<SettingsValue["source"]>
              label="Data source"
              value={settings.source}
              options={[
                { value: "live", label: "Live daemon" },
                { value: "mock", label: "Simulated rig (in browser)" },
              ]}
              onChange={(source) => onChange({ source })}
              help="The simulation runs entirely in this page. Nothing reaches hardware."
            />
            {settings.source === "live" && (
              <TextField
                label="API address"
                optional
                value={settings.apiBase}
                placeholder="blank = same origin (the dev server proxies to :8000)"
                onChange={(apiBase) => onChange({ apiBase })}
                help="e.g. http://raspberrypi.local:8000"
              />
            )}
          </div>
        </Card>

        <Card title="What this rig supports" hint="probed on connect">
          <div className="stack tight">
            {FEATURES.map((feature) => (
              <div className="row" key={feature.key} style={{ justifyContent: "space-between" }}>
                <span>
                  {feature.label} <span className="hint mono">{feature.note}</span>
                </span>
                {rig.capabilities[feature.key] ? (
                  <Badge tone="good">yes</Badge>
                ) : (
                  <Badge tone="warning">no</Badge>
                )}
              </div>
            ))}
            <p className="hint" style={{ margin: 0 }}>
              Anything marked with an R number is specified in{" "}
              <span className="mono">ui/API-REQUIREMENTS.md</span>.
            </p>
          </div>
        </Card>

        <Card title="Display">
          <div className="stack tight">
            <SelectField<SettingsValue["theme"]>
              label="Theme"
              value={settings.theme}
              options={[
                { value: "system", label: "Follow the system" },
                { value: "light", label: "Light" },
                { value: "dark", label: "Dark" },
              ]}
              onChange={(theme) => onChange({ theme })}
            />
            <NumberField
              label="Samples kept"
              value={settings.historyPoints}
              min={60}
              max={20000}
              step={60}
              onChange={(historyPoints) => onChange({ historyPoints: historyPoints ?? 1800 })}
              help="At one sample a second, 1800 is half an hour. Held in this page only."
            />
            <NumberField
              label="Default chart window"
              unit="s"
              value={settings.windowSeconds}
              min={30}
              step={30}
              onChange={(windowSeconds) => onChange({ windowSeconds: windowSeconds ?? 300 })}
            />
            <Toggle
              label="Confirm before anything moves the pumps"
              checked={settings.confirmActions}
              onChange={(confirmActions) => onChange({ confirmActions })}
            />
            <button type="button" onClick={rig.clearHistory}>
              Clear chart history
            </button>
          </div>
        </Card>
      </div>

      <Card
        title="Simulated rig"
        hint={settings.source === "mock" ? "in use" : "applies when the source is the simulation"}
      >
        <div className="stack tight">
          <p className="hint" style={{ margin: 0 }}>
            The switches below exist to exercise the cases a real rig may or may not have. Turn the
            line sensors off to see how the interface reads without them.
          </p>
          <Toggle
            label="Dry-line sensor fitted"
            checked={settings.mock.drySensor}
            onChange={(drySensor) => onChange({ mock: { ...settings.mock, drySensor } })}
          />
          <Toggle
            label="Wet-line sensor fitted"
            checked={settings.mock.wetSensor}
            onChange={(wetSensor) => onChange({ mock: { ...settings.mock, wetSensor } })}
          />
          <Toggle
            label="In-line flow meters fitted"
            checked={settings.mock.flowSensors}
            onChange={(flowSensors) => onChange({ mock: { ...settings.mock, flowSensors } })}
            help="R5 — no rig reports these today"
          />
          <Toggle
            label="Fail the process sensor"
            checked={settings.mock.faultProcess}
            onChange={(faultProcess) => onChange({ mock: { ...settings.mock, faultProcess } })}
            help="shows the fault path"
          />
          <div className="grid cols-2" style={{ gap: 8 }}>
            <NumberField
              label="Dry source humidity"
              unit="%"
              min={0}
              max={100}
              step={0.5}
              value={settings.mock.dryHumidity}
              onChange={(dryHumidity) =>
                onChange({ mock: { ...settings.mock, dryHumidity: dryHumidity ?? 0 } })
              }
            />
            <NumberField
              label="Wet source humidity"
              unit="%"
              min={0}
              max={100}
              step={0.5}
              value={settings.mock.wetHumidity}
              onChange={(wetHumidity) =>
                onChange({ mock: { ...settings.mock, wetHumidity: wetHumidity ?? 100 } })
              }
            />
            <NumberField
              label="Chamber time constant"
              unit="s"
              min={1}
              step={1}
              value={settings.mock.tau}
              onChange={(tau) => onChange({ mock: { ...settings.mock, tau: tau ?? 12 } })}
            />
            <NumberField
              label="Sensor noise"
              unit="% peak"
              min={0}
              step={0.05}
              value={settings.mock.noise}
              onChange={(noise) => onChange({ mock: { ...settings.mock, noise: noise ?? 0 } })}
            />
          </div>
        </div>
      </Card>
    </div>
  );
}
