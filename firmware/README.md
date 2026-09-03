# humctrl firmware

Rust firmware for the board that sits between the host and the pumps.

Scaffolding only. Nothing is implemented — `pico/src/main.rs` initialises the
chip and idles, and `core` is an empty `no_std` crate. Both build and both are
flashable, so the toolchain is verified before any firmware is written.

The job the firmware has to do is set out in [../HANDOFF.md](../HANDOFF.md) §6.3:
read the sensors on a timer, apply the PWM values it is told to, and cut the
pumps if no command arrives within N seconds. The control loop stays on the
host — see [../DECISIONS.md](../DECISIONS.md) D-001, which is still open.

## Layout

```
core/    no_std, no HAL, no peripherals — builds for any MCU, tested on the host
pico/    RP2040 / RP2350 binary: embassy, feature-gated per chip
```

The split exists so the board can change without the logic changing. Anything
that can be written without a peripheral belongs in `core`, where `cargo test`
can reach it; anything that touches hardware belongs in a board crate. Adding a
second board — an Arduino, or anything else — means a third crate alongside
`pico`, not a rewrite.

`core` is the only default workspace member, so a bare `cargo build` or
`cargo test` here builds for the host and does not try to cross-compile.

## Toolchain

Stable Rust. [`rust-toolchain.toml`](rust-toolchain.toml) pins the channel and
pulls in both targets, so `rustup` installs them on first build.

Flashing needs `picotool`, which is not a Rust tool:

```sh
sudo apt install picotool          # or build from raspberrypi/picotool
```

`.cargo/config.toml` sets it as the cargo runner for both targets. Swap the
`runner` line for `probe-rs run --chip RP2040` / `--chip RP235x` if you attach a
debug probe — the BOM in HANDOFF §11 does not include one, so USB CDC is
assumed to be the only channel to the board.

## Building

Which chip is a cargo feature, and each chip needs a different target and
linker script, so both are wrapped in an alias:

| Board             | Chip    | Command                    |
| ----------------- | ------- | -------------------------- |
| Pico, Pico W      | RP2040  | `cargo pico1`              |
| Pico 2, Pico 2 W  | RP2350A | `cargo pico2` *(default)*  |

`cargo pico1-check` / `cargo pico2-check` type-check without linking.
`cargo pico1-run` / `cargo pico2-run` build and flash: hold BOOTSEL while
plugging the board in, then run it.

```sh
cargo test        # core, on the host
cargo pico2       # firmware, for a Pico 2 W
cargo pico2-run   # ... and flash it
```

Selecting neither chip feature, or both, is a build error rather than a silent
mis-link.

## Chip differences

Contained entirely in `pico/build.rs` and the two linker scripts:

- **RP2040** is Cortex-M0+, `thumbv6m-none-eabi`, no FPU and no atomic
  compare-and-swap — hence `portable-atomic/critical-section`. It boots via a
  256-byte second-stage bootloader at the base of flash, which `link-rp.x`
  (supplied by `embassy-rp`) places at `.boot2`.
- **RP2350A** is Cortex-M33, `thumbv8m.main-none-eabihf`, hardware float. It
  boots from an image header instead, which `memory-rp2350.x` positions after
  the vector table where the boot ROM and `picotool` look for it.

`memory-rp2350.x` is embassy's, with `_stext` aligned to 8 to silence a
`rust-lld` alignment warning.

## Choices worth knowing about

- **No `defmt`.** It needs a debug probe to be useful, and there isn't one in
  the BOM. Diagnostics have to share the USB CDC link with the protocol.
- **`panic-reset`, not `panic-halt`.** Halting on a panic leaves the PWM
  peripheral running at its last duty — pumps on, host unaware. Resetting
  returns the pins to inputs and the gate pulldowns hold the MOSFETs off.
- **`overflow-checks` on in release.** A wrong duty is worse than a panic, and
  a panic now stops the pumps.
- **`embassy-rp` owns the time driver and the critical-section implementation**,
  so no other crate should enable either.
