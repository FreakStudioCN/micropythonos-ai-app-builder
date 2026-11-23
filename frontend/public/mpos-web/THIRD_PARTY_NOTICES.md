# MicroPythonOS browser runtime notices

This directory includes upstream runtime binaries, preloaded OS files and example apps. It is not relicensed by the App Builder's root MIT license.

License copies shipped alongside these files:

- [MicroPythonOS](licenses/MicroPythonOS.txt)
- [MicroPython Skills](licenses/MicroPython-Skills.txt)
- [LVGL MicroPython composite third-party notices](licenses/lvgl-micropython-composite.txt)
- [MicroPython](licenses/MicroPython.txt)
- [LVGL](licenses/LVGL.txt)

Original notices in the preloaded source files remain unchanged. In particular, the infrared helper files retain Peter Hinch's MIT attribution, and VL53L5CX helper files retain Mark Grosen's MIT/SPDX headers.

The composite license copy includes multiple upstream components and build targets; its inclusion does not assert that every listed component is compiled into this WASM binary. The checkout does not provide a complete build-to-binary license manifest. Bundled apps without their own license declaration, fonts, icons, media and third-party content must be checked against their specific upstream sources before separate redistribution. No new license is granted to those items here.

See the repository's root `THIRD_PARTY_NOTICES.md` for the wider builder/upstream boundary.
