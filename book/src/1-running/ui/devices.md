# Devices

Every device on the rig. Adding or removing a device or a link is done from [Options › Rig file](rig.md),
not here. Where a device's fields come from is [Where a device's options come from](../../2-config/devices/generated.md); what an entry looks like in the file, [Devices](../../2-config/devices/index.md).

!!! tip "At the terminal"
    `flyball devices`, `flyball read ADDRESS`, `flyball demand ADDRESS VALUE`, and a device's own commands as `flyball invoke <device> <command> KEY=VALUE …` -- [Devices and signals](../cli/devices.md). Adding a link or a device has no subcommand; the routes are `POST /api/links` and `/api/devices` ([Composition](../../4-server/api.md#composition)).

## Devices

Every device is listed on **Readings** (`#/readings`, from Options › Pages; the old
`#/devices` address lands there); `#/devices/<name>` is one device alone, as a card: signals grouped by namespace, with a toggle to pivot by `tags`
section where the device has one; commands as cards; the device's
conditions as badges (each held condition, on the device or one of its signals), and `mode`
and `last.*` drawn as the chip and "ran at" lines they are rather
than raw JSON. A device's name anywhere in the app links to its page.

A signal with no value shows `—` and why ("invalid: open circuit", "stale:
device silent", `…` while pending), the last usable value on hover, never the
value before it ([Readings with no value](charts.md#readings-with-no-value)). A
signal's name carries, on hover, what the rig says of it beyond its value: a
demand's readback (`echo`: its reading is what was committed; `sensed`: read
back from the device) and a banded signal's `on_no_value`. A demand the device only
reports (`access: rp`, a readback its commands move, such as a blender's pump
flows) is shown read-only, like a reading: no entry and no **Set**, since the
rig refuses every write to it.

The line under the title lists what the device follows, one per input: `dry ←
hum_sensors.dry.humidity` for one bound to a signal (with its quality when not
`ok`, "stale: device silent"), `dry = 36.5 %RH` for one bound to a number.

The run line under the title says how the device is being read: "polling ·
every 1 s · last read …" normally; "retrying · 4 failed · … · next try …" when
reads keep failing and the rig is backing off (the device stays running and
tries again by itself), with a **Retry now** button to try at once; "stopped"
with **Restart** when it has given up (a device with `reads.give_up_after_s`)
or was stopped.

A command card is one type scale — title, then labels, then controls — and
cards in a row share a height with **Run** on the bottom line. A field's
description is not printed under it: an ⓘ beside the label carries it on
hover (and for a screen reader). A choice of two kinds is two equal halves,
of three or more a stacked list.

A form sends only the arguments the person changed: an argument left as
the form opened it (prefilled with the signal's current value, or at its
default) is left out, and the rig keeps it where it is -- so setting one
line of a pair does not write the other.

A command that changes what drives the device (it has a `sets_mode`, `writes`,
or an argument that sets a demand) and does not interrupt is refused
while a controller regulates one of the device's signals; its card says
so ("refused while blender.humidity regulates: put it in manual first")
and its button is held until that controller is in manual. A command
that interrupts runs, and puts the controller in manual once it has
succeeded.

After a run the card shows what the command returned ("done" when it
returned nothing) and, when it put a regulating controller in manual to do
its work, which ones: "put chamber.rh in manual". A refusal is shown as the
rig words it: a write to a readback names the command that moves it
("moved by the command 'set_flows' (it puts a regulating controller in
manual)").

Under the header, the device's inputs, one per binding: "dry ←
hum_sensors.dry.humidity" for an input bound to an address, "dry = 36.5 %"
for one bound to a number, with the source's quality when it is not ok
("stale: device offline") and the age of its newest reading. Below the
signals, a `driver: values` device lists where each value came from
("rig file", "restored, written by ben at …", or "written by ben at …"),
and a device others follow lists, per signal, the inputs bound to it
(**Followed by**). These refresh every few seconds.

In [Options › Rig file](rig.md), **Add link** builds a link — a bus, a simulated plant, anything a rig
file's `links:` takes — from the rig's schema (`GET /api/rig/schema`): a kind picker, then a
`SchemaForm` for its config. **Add device** builds a device on the rig the same way: a name, a
driver picker, that driver's config as a `SchemaForm` (a `link` field the schema names becomes
a select of the rig's current links once there are any, else a free-form box), a label, a poll
period, how long a value kept from a failed write may wait to be resent
(`retry_max_age_s`; blank: 60 s), and any **Inputs** the driver takes
(`input -> address`, or a number to bind the input to a constant). A config
field that is a number or an object (a blender's `blend_flow`: a flow, or
`{keep: true, fallback}`) offers both by name. Removing a device or a link
takes everything built on it down too, after a confirmation you complete by typing its name; a
link still carrying a device refuses (409) until the device is removed first. Every add or
remove is a rig edit, so the rig restarts on the new version: see
[The rig file](rig.md).
