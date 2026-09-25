# Layering fixtures

Rig-file layering cases shared by the Python (`engine/tests/test_overlay.py`) and Go
(`daemon/internal/rigfile/overlay_test.go`) implementations, so both merge the same way.
Each folder holds the files and an `expected.json`: the merged document for its `layers`.
