InnoWing Spatial Workbench - Portable Windows runtime

Target: 64-bit Windows (AMD64). Extract the complete application ZIP before use.
No Python installation, administrative privileges, login or internet is needed
for local photo browsing and annotation. Use the application's launcher.

Provenance: Copied from the Codex workspace dependencies bundle 26.1009.11318.
Included: CPython 3.12.14, its standard library/DLLs, Pillow 12.3.0 and Pillow's
package metadata and licenses. No other third-party Python package, user config,
environment file or credential is included. tkinter/IDLE/test suites are excluded.

CPython license: LICENSE.txt (Python Software Foundation and bundled notices).
Pillow license and bundled image-codec/font licenses:
Lib/site-packages/pillow-12.3.0.dist-info/licenses/ .
Pillow is distributed under the permissive HPND license with dependency notices.
These license documents are part of the deliverable and must remain included.

python312._pth sets isolated paths to Lib, DLLs, Pillow and the application root.
PYTHONPATH, user site-packages and system Python installations are not used.
The standard-library ssl module uses the included OpenSSL DLLs. SQLite supports
FTS5. JPEG/PNG encode and decode were verified using the portable interpreter.

runtime-manifest.json records the interpreter/component versions and SHA-256
of included files. This runtime is Windows-specific; other operating systems
can use their own Python and install the application's declared dependencies.
