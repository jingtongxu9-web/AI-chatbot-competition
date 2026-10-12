"""Read-only competition integration; write a separate spatial Chroma index.

Uses AI-chatbot-competition's bot.llm.embed and bot.store.get_store without
changing that repository. Text comes only from human notes and confirmed facts.
"""
import argparse
import json
import sys
from pathlib import Path
import zipfile

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
import workbench as w

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--competition-root',type=Path,help='AI-chatbot-competition checkout, read-only')
    parser.add_argument('--index-dir',type=Path,default=APP/'data/spatial-chroma')
    parser.add_argument('--export-dir',type=Path,default=APP/'data/competition-export')
    parser.add_argument('--build-chroma',action='store_true')
    args=parser.parse_args()
    w.init()
    output=args.export_dir.resolve()
    if not output.is_relative_to(APP):
        raise ValueError('Export directory must stay in the new workbench; existing repositories are read-only.')
    output.mkdir(parents=True,exist_ok=True)
    import io
    with zipfile.ZipFile(io.BytesIO(w.export_zip())) as archive:
        for name in archive.namelist():
            destination=(output/name).resolve()
            if not destination.is_relative_to(output):raise ValueError('Invalid export path')
            destination.write_bytes(archive.read(name))
    chunks=json.loads((output/'spatial-chunks.json').read_text(encoding='utf8'))
    print(f'Exported {len(chunks)} evidence documents to {output}')
    if not args.build_chroma:return
    if not args.competition_root:raise ValueError('--build-chroma needs --competition-root')
    root=args.competition_root.resolve();submission=root/'submission_repo'
    if not (root/'README.md').exists() or not (submission/'bot/store.py').exists():
        raise ValueError('Not an AI-chatbot-competition checkout')
    index=args.index_dir.resolve()
    if not index.is_relative_to(APP):raise ValueError('Chroma output must stay in the new workbench; original indices are protected.')
    sys.path.insert(0,str(submission))
    from bot.store import get_store
    from bot.llm import embed
    store=get_store(path=str(index),name='innowing_spatial',reset=False)
    current_ids={c['id'] for c in chunks}
    previous=store.get(include=[])['ids']
    stale=[pid for pid in previous if pid.startswith('spatial_') and pid not in current_ids]
    if stale:store.delete(ids=stale)
    for start in range(0,len(chunks),64):
        batch=chunks[start:start+64]
        store.upsert(ids=[c['id'] for c in batch],documents=[c['text'] for c in batch],embeddings=embed([c['text'] for c in batch]),metadatas=[{k:v for k,v in c['metadata'].items() if isinstance(v,(str,int,float,bool))} for c in batch])
    print(f'Spatial Chroma index: {index} ({store.count()} documents)')

if __name__=='__main__':main()
