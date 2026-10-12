"""Portable release: original photos, reviewed resources and consistent DB copy."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import tempfile
import zipfile

APP=Path(__file__).resolve().parents[1]
WORKSPACE=APP.parent
sys.path.insert(0,str(APP))
import workbench as w

def main():
    destination=WORKSPACE/'deliverables/InnoWing_Spatial_Workbench_Full_2026-10-12.zip'
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():raise FileExistsError('Delivery already exists; choose a new version rather than replacing an existing handoff.')
    w.DB_PATH=APP/'data/workbench.sqlite3'
    root_files=['server.py','workbench.py','processing.py','requirements.txt','README.md','.env.example',
                'start-workbench.ps1','start-workbench.bat','stop-workbench.ps1','stop-workbench.bat']
    files=[APP/name for name in root_files]
    for directory in ['web','assets','runtime','scripts','tests']:
        files.extend(p for p in (APP/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo') and not p.name.startswith('.'))
    # The public source manifest is intentionally retained; transient cached HTML is not.
    files=[p for p in files if not any(part.startswith('.') for part in p.relative_to(APP).parts) or p.name=='.env.example']
    with tempfile.TemporaryDirectory(prefix='release-',dir=APP/'data') as temp:
        temp=Path(temp)
        assert temp.resolve().is_relative_to((APP/'data').resolve())
        database=temp/'workbench.sqlite3'
        with sqlite3.connect(w.DB_PATH,factory=w.ClosingConnection) as source:
            dest=sqlite3.connect(database)
            try:source.backup(dest)
            finally:dest.close()
        with sqlite3.connect(database,factory=w.ClosingConnection) as con:
            counts={name:con.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in ['photos','annotations','candidates','facts','entities']}
            assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert counts['photos']==214 and counts['candidates']>=214
        release={'version':'2.0.0','date':'2026-10-12','platform':'Windows 64-bit portable; Python source is cross-platform',
                 'counts':counts,'nominal_axis_extent_m':[46.56,25.56],'coordinate_origin':'DA/D6','dimension_unit_status':'accepted_mm_pending_verification',
                 'map_region_count':sum(len(f['regions']) for f in w.maps()['floors']),
                 'photo_source_commit':'684cdfdea99c89d464137391450fd844a8b7dcd2','ocr_status':'214 successful whole-image candidates, unverified',
                 'vision_live_validation':'not_run_no_key_configured','offline_tests':'18 passed','browser_workflow':'passed isolated database; desktop and mobile',
                 'private_credentials_included':False,'existing_competition_repository_modified':False}
        (temp/'RELEASE.json').write_text(json.dumps(release,ensure_ascii=False,indent=2),encoding='utf8')
        mappings=[(p,p.relative_to(APP).as_posix()) for p in files]+[(database,'data/workbench.sqlite3'),(temp/'RELEASE.json','RELEASE.json')]
        hashes=[]
        for p,relative in mappings:hashes.append(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+relative)
        (temp/'MANIFEST-SHA256.txt').write_text('\n'.join(hashes)+'\n',encoding='utf8')
        mappings.append((temp/'MANIFEST-SHA256.txt','MANIFEST-SHA256.txt'))
        with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
            for i,(p,relative) in enumerate(mappings,1):
                archive.write(p,'InnoWing-Spatial-Workbench/'+relative)
                if i%100==0:print(f'Packaged {i}/{len(mappings)} files',flush=True)
        with zipfile.ZipFile(destination) as archive:
            assert archive.testzip() is None
            photo_entries=[name for name in archive.namelist() if '/assets/photos/' in name and not name.endswith('/')]
            assert len(photo_entries)==214
            assert not any('qa-' in name or '.env'==Path(name).name or '/__pycache__/' in name for name in archive.namelist())
            print('Archive CRC verified:',len(archive.namelist()),'files;',len(photo_entries),'original photos',flush=True)
    print('Release:',destination,flush=True)
    print('Size:',destination.stat().st_size,'bytes',flush=True)

if __name__=='__main__':main()
