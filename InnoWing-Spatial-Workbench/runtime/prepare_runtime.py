"""Build the Windows portable runtime from an existing CPython distribution.

Only CPython, its standard library and Pillow are selected. No environment,
user configuration, credentials, or unrelated third-party packages are copied.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def copy_tree(source, target, exclusions=()):
    for directory, children, files in os.walk(source):
        children[:] = sorted(x for x in children if x not in exclusions)
        relative = Path(directory).relative_to(source)
        out = target / relative
        out.mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            if not name.endswith(('.pyc', '.pyo')):
                shutil.copy2(Path(directory) / name, out / name)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', required=True, type=Path)
    args = ap.parse_args()
    source = args.source.resolve()
    root = Path(__file__).resolve().parent
    for name in ('python.exe', 'python3.dll', 'python312.dll', 'LICENSE.txt'):
        shutil.copy2(source / name, root / name)
    for path in source.glob('vcruntime*.dll'):
        shutil.copy2(path, root / path.name)
    copy_tree(source / 'DLLs', root / 'DLLs', {'__pycache__', 'test', 'tests'})
    copy_tree(source / 'Lib', root / 'Lib',
              {'site-packages', '__pycache__', 'test', 'tests', 'tkinter', 'idlelib'})
    site = source / 'Lib/site-packages'
    copy_tree(site / 'PIL', root / 'Lib/site-packages/PIL', {'__pycache__', 'test', 'tests'})
    for path in site.glob('pillow*'):
        if path.is_dir():
            copy_tree(path, root / 'Lib/site-packages' / path.name, {'__pycache__', 'test', 'tests'})
    # Explicitly isolated lookup protects portability from PYTHONPATH, registry,
    # and packages installed on the recipient's computer. '..' is the app root.
    (root / 'python312._pth').write_text('Lib\nDLLs\nLib/site-packages\n..\n', encoding='ascii')
    check = '''import sys, json, platform, ssl, sqlite3, PIL
from PIL import Image
db=sqlite3.connect(':memory:')
db.execute('CREATE VIRTUAL TABLE evidence USING fts5(body)')
db.execute("INSERT INTO evidence(body) VALUES ('portable innowing photo')")
assert db.execute("SELECT COUNT(*) FROM evidence WHERE evidence MATCH 'innowing'").fetchone()[0]==1
import io
image=Image.new('RGB',(8,8),'white')
for fmt in ['JPEG','PNG']:
    out=io.BytesIO(); image.save(out,format=fmt); out.seek(0)
    with Image.open(out) as loaded: loaded.load(); assert loaded.size==(8,8)
assert all('site-packages' not in p or '/runtime/' in p.replace('\\\\','/') for p in sys.path)
print(json.dumps({'python':platform.python_version(),'architecture':platform.machine(),'pillow':PIL.__version__,'sqlite':sqlite3.sqlite_version,'fts5':True,'openssl':ssl.OPENSSL_VERSION,'pillow_decode_encode':True,'isolated':bool(sys.flags.isolated),'sys_path':sys.path}))
'''
    result = subprocess.run([str(root / 'python.exe'), '-B', '-c', check],
                            check=True, capture_output=True, text=True, encoding='utf8')
    verified = json.loads(result.stdout)
    verified['sys_path'] = [Path(os.path.relpath(path, root)).as_posix() for path in verified['sys_path']]
    (root / 'README.txt').write_text('''InnoWing Spatial Workbench - Portable Windows runtime

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
''', encoding='utf8')
    files = []
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.name != 'runtime-manifest.json' and '__pycache__' not in path.parts:
            files.append({'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size,
                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {'version': 1, 'source': 'Codex workspace dependencies bundle 26.1009.11318',
                'target': 'Windows AMD64', 'verification': verified,
                'file_count': len(files), 'total_bytes': sum(x['bytes'] for x in files),
                'files': files}
    (root / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    print(json.dumps({key: manifest[key] for key in ('file_count', 'total_bytes', 'target')}))
    print(json.dumps(verified))


if __name__ == '__main__':
    main()
