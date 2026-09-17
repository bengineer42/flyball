# Controllers and tuning

!!! tip "In the browser"
    [Controllers](../ui/controllers.md) shows each controller as a faceplate with the target box and manual / regulate; [Tuning and autotune](../autotune.md) runs a test and offers the tuning.

| command | |
| --- | --- |
| `flyball controllers` | every controller's view: mode, setpoint, reading, demand, the law and its gains (`GET /api/controllers`) |
| `flyball demand ADDRESS VALUE` | drive a controller's target directly -- refused (409) while the controller is regulating it |
| `flyball invoke <device> <command>` | a device's own commands, including one a driver marks as its demand's (`set_demand` is synthesised where there is none) |

Regulating, going to manual and choosing a tuning have no subcommand yet;
the routes are `POST /api/controllers/{address}/regulate {"at": …}`,
`POST …/manual` and `PUT /api/tunings/{tag}`
([Controllers](../../4-server/api.md#controllers)), reachable from the
CLI's client:

```python
from flyball.client import Rig
rig = Rig("http://pi:8000")
rig.post("/api/controllers/heaters.heater1/regulate", {"at": 400})
```

What a tuning file holds and where it lives: [Controllers](../../2-config/controllers.md#tunings).
