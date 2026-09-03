//! Board-independent firmware logic for the humctrl rig.
//!
//! Nothing in this crate may touch a peripheral, a clock or an allocator, so
//! that it builds for any MCU and runs under `cargo test` on the host. The
//! board crates (`../../pico`) supply the hardware.

#![no_std]
