//! humctrl firmware skeleton for RP2040 and RP2350 boards.
//!
//! Brings up the chip and does nothing else. The firmware proper — sensor
//! sampling, PWM, the USB CDC line protocol and the command watchdog — goes
//! here; see `../../README.md`.

#![no_std]
#![no_main]

// Resets on panic rather than halting, so a fault cannot leave the pumps
// running at their last duty. Reset returns the PWM pins to inputs and the
// gate pulldowns hold the MOSFETs off.
use panic_reset as _;

use embassy_executor::Spawner;
use embassy_time::{Duration, Timer};

#[cfg(not(any(feature = "rp2040", feature = "rp2350")))]
compile_error!("enable exactly one of the `rp2040` / `rp2350` features");
#[cfg(all(feature = "rp2040", feature = "rp2350"))]
compile_error!("features `rp2040` and `rp2350` are mutually exclusive");

#[embassy_executor::main]
async fn main(_spawner: Spawner) {
    let _peripherals = embassy_rp::init(Default::default());

    loop {
        Timer::after(Duration::from_secs(1)).await;
    }
}
