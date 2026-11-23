# Third-party notices

The root MIT license covers project-owned MicroPythonOS AI App Builder code. It does not relicense upstream code, binary runtimes, bundled apps or assets. Contributor attribution is collective and does not transfer anyone's copyright. Existing source headers and submodules remain unchanged.

## Sources and licenses

| Material | Location / origin | Terms and attribution |
| --- | --- | --- |
| MicroPythonOS | `vendor/MicroPythonOS/`, [upstream](https://github.com/MicroPythonOS/MicroPythonOS) | MIT, copyright 2025 MicroPythonOS; retain its LICENSE and component-specific terms |
| MicroPython Skills | `vendor/MicroPython_Skills/`, FreakStudioCN upstream | MIT, copyright 2026 leezisheng; retain its LICENSE |
| Browser runtime and preloaded OS files | `frontend/public/mpos-web/` | Upstream MicroPythonOS, MicroPython, LVGL and build-component terms; [distributed notices](frontend/public/mpos-web/THIRD_PARTY_NOTICES.md) and [license copies](frontend/public/mpos-web/licenses) are included |
| MicroPython | Upstream runtime dependency | MIT and per-component exceptions, including Damien P. George and other original holders; see the full upstream license copy |
| LVGL and its MicroPython bindings | Upstream runtime dependencies | MIT and component-specific terms; Kevin G. Schlosser, LVGL Kft and other original holders are retained in the license copies |
| Infrared helpers | `.preload_bundled_apps/com.micropythonos.ir_remote/ir/` within the browser runtime | Peter Hinch's MIT notices and copyright years remain in the source |
| VL53L5CX helpers | `.preload_bundled_apps/com.micropythonos.time_of_flight/vl53l5cx/` within the browser runtime | Copyright 2021 Mark Grosen, SPDX MIT; source headers remain unchanged |
| Preloaded apps, icons, fonts and media | `.preload_bundled_apps/`, `.preload_internal_filesystem/` and showcase resources | Original app/asset terms apply. Not all manifests declare a license, so root MIT must not be assumed for these resources |
| React, Vite, QR tooling and Python packages | `frontend/package-lock.json`, backend and runner requirements | Their own package licenses; dependency entries have not been relicensed |
| Board/vendor logos and project screenshots | `frontend/public/sponsors/`, README assets and other images | Respective artwork/trademark rights; no trademark grant or endorsement is implied |

## Runtime and bundled-app provenance limitations

The checked-in WASM binary and preloaded resources do not have a complete build-to-binary component manifest. License copies were taken from the checked-out upstream trees; they preserve known attribution but do not prove that the binary was rebuilt from those exact source revisions. The LVGL MicroPython composite text covers several targets and does not mean every listed component is present in the browser build.

Some bundled application manifests, icons and media do not declare a separate license in this checkout. Preserve their source and headers, and verify the specific upstream app/asset terms before extracting or redistributing them independently. These resources were not modified or granted a new MIT license during this change.

Generated or downloaded visual assets can carry separate source URLs and licenses in their per-session metadata. Preserve those records and attribution with any exported app; the builder's MIT license does not grant rights to third-party search results.

## License history

Earlier builder revisions carried Apache-2.0 at the repository root. Under the maintainer-authorized change, current project-owned code uses MIT. The former text, which contained only the generic appendix copyright placeholder, is retained in [Apache-2.0-historical.txt](docs/licenses/Apache-2.0-historical.txt) for prior-version provenance. It is not the current root license, and it does not override a third party's own terms.

This is a focused source/provenance inventory, not an exhaustive dependency or asset clearance. See [README asset provenance](docs/assets/readme/README.md) for the selected publication images.
