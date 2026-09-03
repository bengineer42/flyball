//! Selects the linker script for the chip the `rp2040`/`rp2350` feature names,
//! and copies it somewhere the linker will find it from a workspace build.

use std::env;
use std::fs;
use std::path::PathBuf;

fn main() {
    let rp2040 = env::var_os("CARGO_FEATURE_RP2040").is_some();
    let rp2350 = env::var_os("CARGO_FEATURE_RP2350").is_some();

    let memory_x = match (rp2040, rp2350) {
        (true, false) => "memory-rp2040.x",
        (false, true) => "memory-rp2350.x",
        (true, true) => panic!("features `rp2040` and `rp2350` are mutually exclusive"),
        (false, false) => panic!("enable exactly one of the `rp2040` / `rp2350` features"),
    };

    let out = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR"));
    fs::copy(memory_x, out.join("memory.x")).expect("copy linker script");
    println!("cargo:rustc-link-search={}", out.display());
    println!("cargo:rerun-if-changed={memory_x}");
    println!("cargo:rerun-if-changed=build.rs");

    // `--nmagic` stops the linker page-aligning sections, which on a part with
    // no MMU only wastes flash.
    println!("cargo:rustc-link-arg-bins=--nmagic");
    println!("cargo:rustc-link-arg-bins=-Tlink.x");
    if rp2040 {
        // Places the second-stage bootloader embassy-rp embeds at .boot2.
        // The RP2350 boot ROM reads an image header instead, which memory.x
        // positions itself.
        println!("cargo:rustc-link-arg-bins=-Tlink-rp.x");
    }
}
