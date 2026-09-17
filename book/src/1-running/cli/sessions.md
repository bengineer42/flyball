# Sessions and export

!!! tip "In the browser"
    [Sessions](../ui/sessions.md): record, the rolling record, keep a range, pin, and the export menu; every chart's toolbar downloads what it shows.

| command | |
| --- | --- |
| `flyball sessions` | recorded sessions, newest first, the open one and the scratch record marked (`GET /api/history/sessions`) |
| `flyball export ID [--format csv\|json\|zip] [--out PATH]` | one session as a table, written to `PATH` or stdout (`GET /api/history/sessions/{id}/export`) |

Starting and stopping a recording, keeping a range of the scratch record and
pinning have no subcommand yet: `POST /api/recording`, `POST
/api/recording/end`, `POST /api/history/sessions/{id}/keep`, `PUT
/api/history/sessions/{id} {"pinned": true}` ([Recording](../../4-server/api.md#recording),
[History](../../4-server/api.md#history)).

## Files by URL

The export routes are plain `GET`s, so `curl` (or a browser) is the CLI:

```
curl -O -J "http://pi:8000/api/history/sessions/4/export?format=zip"                 # everything
curl "http://pi:8000/api/history/sessions/4/export?format=csv&layout=wide&step_s=1" > s4.csv
curl "http://pi:8000/api/history/sessions/4/series/furnace.zone1/export?format=csv" > zone1.csv
```

`?token=T` on the URL for a runner started with a token (a plain download cannot
set a header; a browser signed in at the UI needs nothing, its cookie rides
along). Formats and layouts: [History](../../4-server/api.md#history).
