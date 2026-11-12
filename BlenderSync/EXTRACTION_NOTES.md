# Extraction notes

This repository was reconstructed from the supplied 7,985-line pasted-text source.

## Included

- `docs/`: architecture, protocol, decisions, limitations, troubleshooting, and quick-start documentation.
- `blender_extension/`: Blender 5.x extension source, binary protocol, mesh/attribute/material/instance extraction, UI, transport, and Python tests.
- `unity_package/com.blendersync/`: Unity 6 URP package, runtime/editor C# sources, shaders, tests, and sample-scene generator.

## Cleanup performed

- Removed UI transcript artifacts and separator lines that had leaked into source sections.
- Rejoined source fragments explicitly marked as continuations.
- Removed a visibly truncated duplicate Python line in `instances.py` and a malformed, unused conditional import in the Python test file.
- Removed a visibly incomplete duplicate shader pragma from `BlenderSyncLit.shader`.

## Verification performed

- All included Python files pass Python syntax compilation.
- The 4 portable tests in `blender_extension/tests/test_blender_pipeline.py` pass when run without importing the Blender-only add-on entry point.
- The full add-on has not been launched inside Blender, and the Unity package has not been compiled inside Unity. API and editor/runtime behavior still need verification in Blender 5.x and Unity 6 (URP 17.x).

This is an extracted source package, not a certified or production-validated release.
