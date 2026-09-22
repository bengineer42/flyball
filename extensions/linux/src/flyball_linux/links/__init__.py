"""One class per bus, each a protocol with a real and a fake implementation.

A device takes the protocol; a rig file names the link. The fakes remember
every operation and answer from a table, so a device is tested to the byte
without hardware.
"""
