/* RP2040 — Pico, Pico W. 2 MiB flash, 264 KiB RAM. */
MEMORY {
    /* The second-stage bootloader embassy-rp embeds, placed by link-rp.x. */
    BOOT2 : ORIGIN = 0x10000000, LENGTH = 0x100
    FLASH : ORIGIN = 0x10000100, LENGTH = 2048K - 0x100

    /* All six banks as one striped block. Split SCRATCH_A/SCRATCH_B out only
       if something wants RAM with predictable access time. */
    RAM   : ORIGIN = 0x20000000, LENGTH = 264K
}
