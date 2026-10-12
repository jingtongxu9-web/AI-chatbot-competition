"""Loopback-only application server; standard library + Pillow."""
from __future__ import annotations
import argparse
import json
import mimetypes
import os
import sys
import sqlite3
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import workbench as w
import processing as p

class Handler(BaseHTTPRequestHandler):
    server_version="InnoWingWorkbench/2"
    def log_message(self,fmt,*args):
        if self.path.startswith('/api/jobs'):
            return
        print(fmt % args,file=sys.stderr)
    def send(self,data,status=200,mime='application/json; charset=utf-8',filename=None):
        if not isinstance(data,bytes):
            data=w.encode(data).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff')
        if filename:
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers();self.wfile.write(data)
    def read(self,raw=False):
        length=int(self.headers.get('Content-Length','0'))
        if length>100*1024*1024:
            raise ValueError('文件超过 100 MB')
        content=self.rfile.read(length)
        return content if raw else json.loads(content or '{}')
    def error(self,exc):
        status=409 if isinstance(exc,RuntimeError) else 404 if isinstance(exc,KeyError) else 400
        self.send({'error':str(exc)},status)
    def do_GET(self):
        try:
            parsed=urllib.parse.urlsplit(self.path);route=parsed.path;q=urllib.parse.parse_qs(parsed.query)
            if route=='/api/bootstrap':
                self.send({'maps':w.maps(),'photos':w.list_photos(),'entities':w.entities(),'config':p.config(),'jobs':p.get_jobs()});return
            if route=='/api/photos':
                self.send(w.list_photos());return
            if route.startswith('/api/photo/'):
                self.send(w.get_photo(route.rsplit('/',1)[1]));return
            if route.startswith('/api/image/'):
                pid=route.rsplit('/',1)[1]
                size=q.get('size',['2048'])[0]
                file=w.original_preview(pid) if size=='original' else w.thumbnail(pid,size)
                self.send(file.read_bytes(),mime=mimetypes.guess_type(file)[0] or 'image/jpeg');return
            if route=='/api/jobs':
                self.send(p.get_jobs());return
            if route=='/api/coverage':
                self.send(w.coverage_report());return
            if route=='/api/entity-report':
                self.send(w.entity_report());return
            if route=='/api/distances':
                self.send(w.region_distances());return
            if route=='/api/search':
                self.send(w.search(q.get('q',[''])[0]));return
            if route=='/api/export/json':
                self.send(json.dumps(w.snapshot(),ensure_ascii=False,indent=2).encode(),mime='application/json; charset=utf-8',filename='innowing-annotations.json');return
            if route=='/api/export/zip':
                self.send(w.export_zip(),mime='application/zip',filename='innowing-spatial-export.zip');return
            if route=='/api/export/database':
                file=w.backup_database();self.send(file.read_bytes(),mime='application/vnd.sqlite3',filename=file.name);return
            if route=='/api/config':
                self.send(p.config());return
            if route=='/api/health':
                self.send({'ok':True,'photos':len(w.list_photos()),'version':'2.0.0','test_instance':w.DB_PATH.name.startswith('qa-')});return
            relative='web/index.html' if route=='/' else route.lstrip('/')
            if not(relative.startswith('web/') or relative.startswith('assets/')):
                raise KeyError('文件不存在')
            path=w.safe_file(urllib.parse.unquote(relative))
            if not (path.is_relative_to(w.ROOT/'assets') or path.is_relative_to(w.ROOT/'web')):
                raise KeyError('文件不存在')
            if not path.is_file():
                raise KeyError('文件不存在')
            mime=mimetypes.guess_type(path)[0] or 'application/octet-stream'
            self.send(path.read_bytes(),mime=mime+'; charset=utf-8' if mime.startswith('text/') or mime=='application/javascript' else mime)
        except (ValueError,KeyError,RuntimeError,OSError,json.JSONDecodeError,sqlite3.Error) as exc:
            self.error(exc)
    def do_POST(self):
        origin=self.headers.get('Origin')
        if origin and origin!=f'http://{self.headers.get("Host")}':
            self.send({'error':'需要从本地应用页面操作'},403);return
        try:
            parsed=urllib.parse.urlsplit(self.path);route=parsed.path;q=urllib.parse.parse_qs(parsed.query)
            if route=='/api/upload':
                self.send(w.import_photo(self.read(raw=True),q.get('path',['image.jpg'])[0]));return
            value=self.read()
            if route=='/api/save':self.send(w.save_annotation(value['photo_id'],value['annotation'],value.get('expected_revision')))
            elif route=='/api/undo':self.send(w.undo(value['photo_id'],value.get('history_id')))
            elif route=='/api/batch':self.send(w.batch_annotations(value['photo_ids'],value['patch']))
            elif route=='/api/entity':self.send(w.save_entity(value))
            elif route=='/api/room-name':self.send(w.confirm_room_name(value['floor'],value['region_id'],value['name'],value.get('note','')))
            elif route=='/api/relation':self.send(w.relation(value))
            elif route=='/api/relation/delete':self.send(w.delete_relation(value['id']))
            elif route=='/api/candidate/review':self.send(w.review_candidate(value['id'],value['state'],value.get('edited_text')))
            elif route=='/api/candidate/import':self.send({'id':w.add_candidate(value['photo_id'],value.get('kind','ocr_import'),value.get('engine','imported'),value['text'],value.get('payload',{}),value.get('raw',value['text']))})
            elif route=='/api/fact':self.send(w.save_fact(value))
            elif route=='/api/process':self.send(p.start_job(value['photo_ids'],value['mode'],value.get('tiles',False)))
            elif route=='/api/job/cancel':self.send(p.cancel_job(value['id']))
            elif route=='/api/config':self.send(p.save_config(value))
            elif route=='/api/reload-manifest':self.send(w.ingest_manifest())
            elif route=='/api/restore':self.send(w.restore_snapshot(value))
            elif route=='/api/reindex':self.send(w.rebuild_all_search())
            else:raise KeyError('API 不存在')
        except (ValueError,KeyError,RuntimeError,OSError,json.JSONDecodeError,TypeError,sqlite3.Error) as exc:
            self.error(exc)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--open',action='store_true');args=parser.parse_args()
    w.init();w.rebuild_all_search()
    for port in range(args.port,args.port+20):
        try:server=ThreadingHTTPServer(('127.0.0.1',port),Handler);break
        except OSError:
            if port==args.port+19:raise
    url=f'http://127.0.0.1:{port}'
    print(url,flush=True)
    if not w.DB_PATH.name.startswith('qa-'):
        (w.DATA/'server-url.txt').write_text(url,encoding='utf8')
        (w.DATA/'server-pid.txt').write_text(str(os.getpid()),encoding='utf8')
    if args.open:threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
