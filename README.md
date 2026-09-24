# flyball

Control anything with sensors and actuators.

Link any input to any output through controllers (PID, autotune), hold or move setpoints,
and run whole programs across the system. Set it up with no code in the browser, or load
a config someone sent you; every rig gets a drag-and-drop web UI. Adapters reach most
hardware, signals carry their units, access is controlled, and an MCP server lets AI
models drive it. A lab test rig, an oven, a grow tent or a dosing skid: same file format,
same UI.

## The web UI

Generated from the running rig, with no front-end code per rig. Every readout and every
input already knows its unit, its safe range and its current value.

- **Dashboards** of readouts, charts and controller panels. New rigs get a generated overview; drag and drop to build your own.
- **Commands**: a device's own actions. Define one once and it is a card in the UI, an API route, a `flyball invoke` call and a program step.
- **Programs** built by dragging steps together, checked against the rig as you go.
- **Recording** and export, and on a simulated rig, playback of what happened.
- **Build a whole rig in the browser** and save it as a file to share; dashboards kept beside the file load with it. Or start from a hand-written file and make changes in the UI.

[The UI](https://bengineer42.github.io/flyball/latest/1-running/ui/) · [Dashboards](https://bengineer42.github.io/flyball/latest/1-running/dashboards/)

## Quick start

**Operating a rig someone set up.** Open its address in a browser. The UI is the whole
rig: readouts, charts, controllers, programs, recordings.
[Running a rig](https://bengineer42.github.io/flyball/latest/1-running/)

**Building a rig.** One file says what is on it. Start simulated, then swap in real
hardware.

```
cd engine && uv sync --all-extras && cd ../ui && npm install && npm run build
cd ../daemon && CGO_ENABLED=0 go build ./cmd/flyball
./flyball run ../examples/simulated/oven.yaml --serve-ui :8000
```

[Installing](https://bengineer42.github.io/flyball/latest/1-running/runner/#installing) ·
[Configuration](https://bengineer42.github.io/flyball/latest/2-config/) ·
[Drivers](https://bengineer42.github.io/flyball/latest/2-config/devices/drivers/): SCPI, Modbus, I²C, QCoDeS, PyMeasure with no code ·
[Raspberry Pi boards](https://bengineer42.github.io/flyball/latest/2-config/boards/)

**Talking to a rig from code or a model.** HTTP and websocket API, a Python client, MCP,
and a Go CLI and daemon with the UI built in.
[The server](https://bengineer42.github.io/flyball/latest/4-server/) ·
[Access](https://bengineer42.github.io/flyball/latest/1-running/runner/access/): passwords and tokens

**Adding a driver or a control law.** One Python class.
[Extending](https://bengineer42.github.io/flyball/latest/3-extending/)

**Contributing.** [CONTRIBUTING.md](CONTRIBUTING.md) ·
[Internals](https://bengineer42.github.io/flyball/latest/6-internals/)

The whole book starts at [bengineer42.github.io/flyball](https://bengineer42.github.io/flyball/latest/).
The reference application, a humidity chamber on a Raspberry Pi, is
[humctrl](https://github.com/bengineer42/humctrl). [MIT](LICENSE) licensed.
