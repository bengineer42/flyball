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

After a run the card shows what the command returned ("done" when it
returned nothing) and, when it put a regulating controller in manual to do
its work, which ones: "put chamber.rh in manual".

In [Options › Rig file](rig.md), **Add link** builds a link — a bus, a simulated plant, anything a rig
file's `links:` takes — from the rig's schema (`GET /api/rig/schema`): a kind picker, then a
`SchemaForm` for its config. **Add device** builds a device on the rig the same way: a name, a
driver picker, that driver's config as a `SchemaForm` (a `link` field the schema names becomes
a select of the rig's current links once there are any, else a free-form box), a label, a poll
period, and any **Inputs** (`input -> address`) the driver takes. Removing a device or a link
takes everything built on it down too, after a confirmation naming what that is; a link still
carrying a device refuses (409) until the device is removed first.
