# Devices

Every device on the rig, and the dialogs that add one. Where a device's fields come from is [Where a device's options come from](../../2-config/devices/generated.md); what an entry looks like in the file, [Devices](../../2-config/devices/index.md).

!!! tip "At the terminal"
    `flyball devices`, `flyball read ADDRESS`, `flyball demand ADDRESS VALUE`, and a device's own commands as `flyball invoke <device> <command> KEY=VALUE …` -- [Devices and signals](../cli/devices.md). Adding a link or a device has no subcommand; the routes are `POST /api/links` and `/api/devices` ([Composition](../../4-server/api.md#composition)).

## Devices

**Devices** (`#/devices`, or `#/devices/<name>` for one alone) is a card
per device: signals grouped by namespace, with a toggle to pivot by `tags`
section where the device has one; commands as cards; `conditions`, `mode`
and `last.*` drawn as the list, chip and "ran at" lines they are rather
than raw JSON. In the side menu the **Devices** entry opens (a chevron,
open by itself while a device page is showing) into one link per device,
so a device is one click from anywhere.

A command card is one type scale — title, then labels, then controls — and
cards in a row share a height with **Run** on the bottom line. A field's
description is not printed under it: an ⓘ beside the label carries it on
hover (and for a screen reader). A choice of two kinds is two equal halves,
of three or more a stacked list.

- **Add link** builds a link — a bus, a simulated plant, anything a rig
  file's `links:` takes — from the rig's schema (`GET /api/rig/schema`): a
  kind picker, then a `SchemaForm` for its config.
- **Add device** builds a device on the rig the same way: a name, a driver
  picker, that driver's config as a `SchemaForm` (a `link` field the schema
  names becomes a select of the rig's current links once there are any,
  else a free-form box), a label, a poll period, and any bound inputs
  (`role -> address`) the driver takes.
- Removing a device or a link takes everything built on it down too, after
  a confirmation naming what that is; a link still carrying a device
  refuses (409) until the device is removed first.
