"""Local spatial evidence store; never modifies the competition repository."""
from __future__ import annotations
import base64
import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
LOCK = threading.RLock()
DB_PATH = Path(os.getenv("INNOWING_DB", str(DATA / "workbench.sqlite3")))

def now():
    return datetime.now(timezone.utc).isoformat()

def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

def read_json(path, fallback=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback

class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

def database():
    con = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con

def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with database() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS annotations(photo_id TEXT PRIMARY KEY REFERENCES photos(id), value TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY, photo_id TEXT NOT NULL, value TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS relations(id TEXT PRIMARY KEY, photo_id TEXT REFERENCES photos(id), other_id TEXT REFERENCES photos(id), kind TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(photo_id,other_id,kind));
        CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, photo_id TEXT REFERENCES photos(id), kind TEXT NOT NULL, engine TEXT NOT NULL, text TEXT NOT NULL, payload TEXT NOT NULL, raw TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', cache_key TEXT UNIQUE, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY, photo_id TEXT REFERENCES photos(id), value TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        try:
            con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(photo_id UNINDEXED, body, tokenize='unicode61')")
        except sqlite3.OperationalError:
            pass
        # Interrupted jobs are visible and can be restarted; no silent background retry.
        for row in con.execute("SELECT id,value FROM jobs"):
            value = json.loads(row["value"])
            if value.get("state") in ("queued", "running"):
                value.update(state="interrupted", message="服务重启，任务已中断；可重新执行，已有缓存保留。")
                con.execute("UPDATE jobs SET value=? WHERE id=?", (encode(value), row["id"]))
    ingest_manifest()

@lru_cache(maxsize=8)
def _load_maps(region_path,calibration_path,region_stamp,calibration_stamp):
    value = read_json(Path(region_path), {"floors": []})
    value["calibration"] = read_json(Path(calibration_path), {"floors": {}})
    return value

def maps():
    region=ROOT/'assets/map-regions.json';calibration=ROOT/'assets/map-calibration.json'
    return _load_maps(str(region),str(calibration),region.stat().st_mtime_ns if region.exists() else 0,calibration.stat().st_mtime_ns if calibration.exists() else 0)

def metric(floor, x, y):
    cal = maps()["calibration"].get("floors", {}).get(floor)
    if not cal:
        raise ValueError("该楼层尚未加载尺寸校准")
    return [round(cal["x"]["scale"] * x + cal["x"]["offset"], 5),
            round(cal["y"]["scale"] * y + cal["y"]["offset"], 5)]

def inside(point, polygon):
    x, y = point
    result = False
    for i in range(len(polygon)):
        x1, y1 = polygon[i - 1]
        x2, y2 = polygon[i]
        # Include shared boundaries; callers retain all candidates.
        cross = (x-x1)*(y2-y1)-(y-y1)*(x2-x1)
        if abs(cross) < 1e-7 and min(x1,x2)-1e-7 <= x <= max(x1,x2)+1e-7 and min(y1,y2)-1e-7 <= y <= max(y1,y2)+1e-7:
            return True
        if (y1 > y) != (y2 > y) and x < (x2-x1)*(y-y1)/(y2-y1)+x1:
            result = not result
    return result

def regions_at(floor, x, y):
    selected = next((f for f in maps()["floors"] if f["id"] == floor), None)
    return [r["id"] for r in selected["regions"] if inside([x,y], r["points"])] if selected else []

def confirm_room_name(floor_id,region_id,name,note=''):
    name=str(name).strip()
    if not name or len(name)>160:
        raise ValueError('请输入有效的真实房间名称')
    with LOCK:
        value=copy.deepcopy(maps());value.pop('calibration',None)
        floor=next((f for f in value['floors'] if f['id']==floor_id),None)
        region=next((r for r in floor['regions'] if r['id']==region_id),None) if floor else None
        if not region:raise KeyError('空间分区不存在')
        group=region.get('room_id')
        for r in floor['regions']:
            if r['id']==region_id or (group and r.get('room_id')==group):
                old=r['label']
                r.setdefault('aliases',[])
                if old not in r['aliases']:r['aliases'].append(old)
                r.update(label=name,name=name,short_label=name,room_name=name,site_name=name,
                         name_status='human_site_confirmed',site_name_confirmed_at=now(),site_name_note=note,
                         name_verification='human_site_confirmation')
        value['name_revision']=value.get('name_revision',0)+1
        path=ROOT/'assets/map-regions.json'
        backups=DATA/'backups';backups.mkdir(exist_ok=True)
        (backups/f"room-names-{uuid.uuid4().hex[:12]}.json").write_bytes(path.read_bytes())
        temporary=path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
        os.replace(temporary,path)
    rebuild_all_search()
    return maps()

def default_annotation(photo):
    return {"floor": None, "map_version": maps().get("revision", "review-01"), "camera": None,
            "heading_deg": None, "heading_confidence": "unknown", "pitch_mode": "horizontal", "pitch_deg": None,
            "fov_deg": None, "camera_area": None, "position_confidence": "unknown", "status": "unmarked",
            "captured_at": photo.get("captured_at"), "photo_type": "overview", "group_id": "", "tags": [],
            "notes": {"shooting": "", "content": "", "correspondence": "", "uncertainty": ""},
            "targets": [], "coverage": [], "boxes": [], "revision": 0, "updated_at": None}

def ingest_manifest():
    manifest = read_json(ROOT / "assets/photo-manifest.json", {})
    count = 0
    with LOCK, database() as con:
        for record in manifest.get("images", []):
            if record.get("status") == "failed":
                continue
            rec = copy.deepcopy(record)
            digest = rec.get("content_sha256") or rec.get("sha256")
            if not digest:
                continue
            pid = digest[:24]
            rec["id"] = pid
            rec.setdefault("name", Path(rec["relative_path"]).name)
            rec.setdefault("sources", [{k: rec.get(k) for k in ("src", "page", "source_origin", "source_type", "folder_hint")}])
            existing = con.execute("SELECT record FROM photos WHERE sha256=?", (digest,)).fetchone()
            if existing:
                old = json.loads(existing[0])
                for source in rec["sources"]:
                    if source not in old.setdefault("sources", []):
                        old["sources"].append(source)
                con.execute("UPDATE photos SET record=? WHERE id=?", (encode(old), pid))
            else:
                con.execute("INSERT INTO photos VALUES(?,?,?)", (pid,digest,encode(rec)))
                count += 1
    return {"added": count, "failures": manifest.get("failures", [])}

def safe_file(relative):
    resolved = (ROOT / relative).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("文件路径超出应用目录")
    return resolved

def photo_path(pid):
    with database() as con:
        row = con.execute("SELECT record FROM photos WHERE id=?", (pid,)).fetchone()
    if not row:
        raise KeyError("照片不存在")
    return safe_file(json.loads(row[0])["relative_path"])

def import_photo(data, relative):
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(data))
    if im.format not in ("JPEG", "PNG", "WEBP", "GIF", "TIFF"):
        raise ValueError("仅支持 JPEG、PNG、WEBP、GIF 或 TIFF 图片")
    im.verify()
    im = Image.open(io.BytesIO(data))
    exif = im.getexif()
    captured = exif.get(36867) or exif.get(306)
    oriented = ImageOps.exif_transpose(im)
    digest = hashlib.sha256(data).hexdigest()
    pid = digest[:24]
    # Reject path traversal, preserve folders without exposing external filesystem.
    parts = [p for p in relative.replace("\\", "/").split("/") if p]
    if not parts or any(p in ("..", ".") for p in parts):
        raise ValueError("无效的相对文件名")
    name = parts[-1]
    path = Path("assets/imports") / digest[:12] / Path(*parts)
    destination = safe_file(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        destination.write_bytes(data)
    source = {"source_type": "physical", "source_origin": "user_folder", "folder_hint": "/".join(parts[:-1]), "src": None, "page": None}
    record = {"id": pid, "content_sha256": digest, "relative_path": path.as_posix(), "name": name,
              "folder_hint": source["folder_hint"], "source_type": "physical", "source_origin": "user_folder",
              "width": oriented.width, "height": oriented.height, "captured_at": captured,
              "capture_time_source": "EXIF; timezone unspecified" if captured else None,
              "bytes": len(data), "sources": [source]}
    with LOCK, database() as con:
        row = con.execute("SELECT record FROM photos WHERE id=?", (pid,)).fetchone()
        if row:
            old = json.loads(row[0])
            if source not in old.setdefault("sources", []):
                old["sources"].append(source)
            con.execute("UPDATE photos SET record=? WHERE id=?", (encode(old),pid))
        else:
            con.execute("INSERT INTO photos VALUES(?,?,?)", (pid,digest,encode(record)))
    return {"id":pid, "duplicate":bool(row)}

def thumbnail(pid, size=300):
    from PIL import Image, ImageOps
    if not re.fullmatch(r'[a-f0-9]{24}',pid):
        raise ValueError('无效照片编号')
    size = max(100,min(4096,int(size)))
    destination = DATA / "cache" / f"{pid}-{size}.jpg"
    if not destination.exists():
        im = ImageOps.exif_transpose(Image.open(photo_path(pid))).convert("RGB")
        im.thumbnail((size,size))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name+f".{uuid.uuid4().hex}.tmp")
        im.save(temp, "JPEG", quality=90)
        os.replace(temp,destination)
    return destination

def original_preview(pid):
    from PIL import Image,ImageOps
    source=photo_path(pid)
    with Image.open(source) as image:
        orientation=image.getexif().get(274,1)
        if orientation==1:
            return source
        destination=DATA/'cache'/f'{pid}-normalized.png'
        if not destination.exists():
            destination.parent.mkdir(parents=True,exist_ok=True)
            normalized=ImageOps.exif_transpose(image)
            normalized.save(destination,'PNG')
    return destination

def list_photos():
    with database() as con:
        rows = con.execute("SELECT p.id,p.record,a.value,a.revision,a.updated_at FROM photos p LEFT JOIN annotations a ON a.photo_id=p.id ORDER BY p.record").fetchall()
    result=[]
    for row in rows:
        rec=json.loads(row["record"])
        rec["annotation"]=json.loads(row["value"]) if row["value"] else default_annotation(rec)
        rec["annotation"]["revision"]=row["revision"] or 0
        rec["annotation"]["updated_at"]=row["updated_at"]
        result.append(rec)
    return sorted(result,key=lambda p:(p.get("folder_hint", ""),p.get("name",p["relative_path"])))

def get_photo(pid):
    photo=next((p for p in list_photos() if p["id"]==pid),None)
    if not photo:
        raise KeyError("照片不存在")
    with database() as con:
        photo["relations"]=[dict(row) for row in con.execute("SELECT * FROM relations WHERE photo_id=? OR other_id=?",(pid,pid))]
        photo["candidates"]=[{**dict(row),"payload":json.loads(row["payload"])} for row in con.execute("SELECT * FROM candidates WHERE photo_id=? ORDER BY created_at DESC",(pid,))]
        photo["facts"]=[json.loads(row[0]) for row in con.execute("SELECT value FROM facts WHERE photo_id=? ORDER BY created_at",(pid,))]
        photo["history"]=[{"id":row["id"],"revision":row["revision"],"created_at":row["created_at"]} for row in con.execute("SELECT id,revision,created_at FROM revisions WHERE photo_id=? ORDER BY id DESC LIMIT 20",(pid,))]
    return photo

def checked_number(value,low,high,nullable=True):
    if value is None and nullable:
        return None
    number=float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError("数值超出允许范围")
    return number

def validate_annotation(value):
    annotation=copy.deepcopy(value)
    floors={f["id"]:f for f in maps()["floors"]}
    floor=annotation.get("floor")
    if floor is not None and floor not in floors:
        raise ValueError("无效楼层")
    def point(value,floor_id):
        f=floors.get(floor_id)
        if not f:
            raise ValueError("设置位置前请选择楼层")
        x=checked_number(value.get("x_px"),0,f["width"],False)
        y=checked_number(value.get("y_px"),0,f["height"],False)
        m=metric(floor_id,x,y)
        return {**value,"x_px":x,"y_px":y,"x_m":m[0],"y_m":m[1],"area_candidates":regions_at(floor_id,x,y)}
    def polygon(points,floor_id):
        f=floors.get(floor_id)
        if not f or not isinstance(points,list) or len(points)<3:
            raise ValueError("区域需要有效楼层与至少三个点")
        checked=[]
        for p in points:
            if not isinstance(p,list) or len(p)!=2:
                raise ValueError("区域坐标格式无效")
            checked.append([checked_number(p[0],0,f["width"],False),checked_number(p[1],0,f["height"],False)])
        return checked
    if annotation.get("camera"):
        annotation["camera"]=point(annotation["camera"],floor)
    for target in annotation.setdefault("targets",[]):
        target.setdefault("floor",floor)
        if target.get("type","point")=="point":
            target.update(point(target,target["floor"]))
        if target.get("points_px"):
            target["points_px"]=polygon(target["points_px"],target["floor"])
            target["points_m"]=[metric(target["floor"],p[0],p[1]) for p in target["points_px"]]
    for coverage in annotation.setdefault("coverage",[]):
        coverage["points_px"]=validate_polygon(coverage["points_px"],floor,floors)
        coverage["points_m"]=[metric(floor,p[0],p[1]) for p in coverage["points_px"]]
    for box in annotation.setdefault("boxes",[]):
        for key in ("x","y","width","height"):
            box[key]=checked_number(box.get(key),0,1,False)
        if box["x"]+box["width"]>1.000001 or box["y"]+box["height"]>1.000001 or not box["width"] or not box["height"]:
            raise ValueError("照片框选超出图片范围")
    annotation["heading_deg"]=checked_number(annotation.get("heading_deg"),0,360)
    if annotation["heading_deg"]==360:
        annotation["heading_deg"]=0
    annotation["fov_deg"]=checked_number(annotation.get("fov_deg"),1,180)
    annotation["pitch_deg"]=checked_number(annotation.get("pitch_deg"),-90,90)
    if annotation.get("status") not in ("unmarked","marked","review","unknown"):
        raise ValueError("无效标注状态")
    if annotation.get("status")=="marked" and (not annotation.get("camera") or annotation.get("heading_deg") is None):
        raise ValueError("已标注需要拍摄点与朝向；无法确定时请使用待确认或未知状态")
    if annotation.get("camera_area") and floor and annotation["camera_area"] not in {r["id"] for r in floors[floor]["regions"]}:
        raise ValueError("拍摄区域不属于该楼层")
    for field in ('position_confidence','heading_confidence'):
        annotation.setdefault(field,'unknown')
        if annotation[field] not in ('unknown','estimated','confirmed'):
            raise ValueError('无效可信度状态')
    if annotation.get('pitch_mode','horizontal') not in ('horizontal','up','down'):
        raise ValueError('无效俯仰方向')
    annotation["map_version"]=maps().get("revision")
    annotation.setdefault("notes",{})
    annotation.setdefault("boxes",[])
    annotation.setdefault("tags",[])
    return annotation

def validate_polygon(points,floor_id,floors):
    f=floors.get(floor_id)
    if not f or not isinstance(points,list) or len(points)<3:
        raise ValueError("区域需要有效楼层与至少三个点")
    result=[]
    for p in points:
        if not isinstance(p,list) or len(p)!=2:
            raise ValueError("区域坐标格式无效")
        result.append([checked_number(p[0],0,f["width"],False),checked_number(p[1],0,f["height"],False)])
    return result

def rebuild_search(con,pid):
    row=con.execute("SELECT record FROM photos WHERE id=?",(pid,)).fetchone()
    if not row:
        return
    body=[row[0]]
    for row in con.execute("SELECT value FROM annotations WHERE photo_id=?",(pid,)):
        body.append(row[0])
        ann=json.loads(row[0]);floor=next((f for f in maps()["floors"] if f["id"]==ann.get("floor")),None)
        if floor:
            body.extend(r["label"] for r in floor["regions"] if r["id"]==ann.get("camera_area"))
    body.extend(row[0] for row in con.execute("SELECT text FROM candidates WHERE photo_id=? AND state!='rejected'",(pid,)))
    body.extend(row[0] for row in con.execute("SELECT value FROM facts WHERE photo_id=?",(pid,)))
    try:
        con.execute("DELETE FROM evidence_fts WHERE photo_id=?",(pid,))
        con.execute("INSERT INTO evidence_fts VALUES(?,?)",(pid,"\n".join(body)))
    except sqlite3.OperationalError:
        pass

def save_annotation(pid,value,expected=None,restoring=False):
    annotation=validate_annotation(value)
    if not restoring:
        annotation.pop("_undo_cursor",None)
    with LOCK,database() as con:
        if not con.execute("SELECT 1 FROM photos WHERE id=?",(pid,)).fetchone():
            raise KeyError("照片不存在")
        row=con.execute("SELECT * FROM annotations WHERE photo_id=?",(pid,)).fetchone()
        revision=row["revision"] if row else 0
        if expected is not None and int(expected)!=revision:
            raise RuntimeError("标注已在其他窗口更新，请重新加载照片再保存")
        if row:
            con.execute("INSERT INTO revisions(photo_id,value,revision,created_at) VALUES(?,?,?,?)",(pid,row["value"],revision,now()))
        else:
            photo=json.loads(con.execute("SELECT record FROM photos WHERE id=?",(pid,)).fetchone()[0])
            con.execute("INSERT INTO revisions(photo_id,value,revision,created_at) VALUES(?,?,0,?)",(pid,encode(default_annotation(photo)),now()))
        revision+=1
        annotation.update(revision=revision,updated_at=now())
        con.execute("INSERT INTO annotations VALUES(?,?,?,?) ON CONFLICT(photo_id) DO UPDATE SET value=excluded.value,revision=excluded.revision,updated_at=excluded.updated_at",(pid,encode(annotation),revision,annotation["updated_at"]))
        rebuild_search(con,pid)
    return annotation

def undo(pid,history_id=None):
    with database() as con:
        current=con.execute("SELECT value,revision FROM annotations WHERE photo_id=?",(pid,)).fetchone()
        cursor=json.loads(current["value"]).get("_undo_cursor",current["revision"]) if current else 0
        row=con.execute("SELECT value,revision FROM revisions WHERE photo_id=? AND id=?",(pid,history_id)).fetchone() if history_id else con.execute("SELECT value,revision FROM revisions WHERE photo_id=? AND revision<? ORDER BY revision DESC LIMIT 1",(pid,cursor)).fetchone()
    if not row:
        raise ValueError("没有可恢复的历史版本")
    value=json.loads(row["value"]);value["_undo_cursor"]=row["revision"]
    return save_annotation(pid,value,restoring=True)

def batch_annotations(ids,patch):
    allowed={"captured_at","photo_type","floor","camera_area","status","group_id","tags"}
    if not set(patch).issubset(allowed):
        raise ValueError("批量设置仅支持日期、类型、楼层、区域、状态、分组与标签")
    annotations=[]
    for pid in ids:
        p=get_photo(pid);a=p["annotation"]
        if "floor" in patch and patch["floor"]!=a.get("floor"):
            a.update(camera=None,heading_deg=None,camera_area=None,targets=[],coverage=[])
        a.update(patch)
        annotations.append((pid,validate_annotation(a)))
    with LOCK,database() as con:
        con.execute("BEGIN IMMEDIATE")
        for pid,a in annotations:
            row=con.execute("SELECT value,revision FROM annotations WHERE photo_id=?",(pid,)).fetchone()
            rev=row["revision"] if row else 0
            if row:
                con.execute("INSERT INTO revisions(photo_id,value,revision,created_at) VALUES(?,?,?,?)",(pid,row["value"],rev,now()))
            else:
                photo=json.loads(con.execute("SELECT record FROM photos WHERE id=?",(pid,)).fetchone()[0])
                con.execute("INSERT INTO revisions(photo_id,value,revision,created_at) VALUES(?,?,0,?)",(pid,encode(default_annotation(photo)),now()))
            a.update(revision=rev+1,updated_at=now())
            con.execute("INSERT INTO annotations VALUES(?,?,?,?) ON CONFLICT(photo_id) DO UPDATE SET value=excluded.value,revision=excluded.revision,updated_at=excluded.updated_at",(pid,encode(a),a["revision"],a["updated_at"]))
            rebuild_search(con,pid)
    return {"updated":len(annotations)}

def entities():
    with database() as con:
        return [json.loads(row[0]) for row in con.execute("SELECT value FROM entities ORDER BY updated_at DESC")]

def save_entity(value):
    value=copy.deepcopy(value)
    if not str(value.get("name", "")).strip():
        raise ValueError("实体名称不能为空")
    value.setdefault("id","entity_"+uuid.uuid4().hex[:12])
    value.setdefault("aliases",[])
    value["updated_at"]=now()
    with LOCK,database() as con:
        con.execute("INSERT INTO entities VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(value["id"],encode(value),value["updated_at"]))
    return value

def relation(value):
    pid,other=value["photo_id"],value["other_id"]
    if pid==other:
        raise ValueError("不能关联同一张照片")
    kind=value.get("kind","overview_detail")
    if kind not in ("overview_detail","same_object","sequence"):
        raise ValueError("无效关联类型")
    rid="link_"+hashlib.sha256(f"{pid}:{other}:{kind}".encode()).hexdigest()[:20]
    with LOCK,database() as con:
        con.execute("INSERT INTO relations VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET note=excluded.note",(rid,pid,other,kind,value.get("note", ""),now()))
    return {"id":rid}

def delete_relation(rid):
    with LOCK,database() as con:
        con.execute("DELETE FROM relations WHERE id=?",(rid,))
    return {"deleted":rid}

def add_candidate(pid,kind,engine,text,payload,raw,cache_key=None):
    cid="candidate_"+uuid.uuid4().hex[:16]
    with LOCK,database() as con:
        existing=con.execute("SELECT id FROM candidates WHERE cache_key=?",(cache_key,)).fetchone() if cache_key else None
        if existing:
            return existing[0]
        con.execute("INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?)",(cid,pid,kind,engine,text,encode(payload),encode(raw) if not isinstance(raw,str) else raw,"pending",cache_key,now()))
        rebuild_search(con,pid)
    return cid

def save_fact(value):
    value=copy.deepcopy(value)
    if not value.get("photo_id") or not str(value.get("value", "")).strip():
        raise ValueError("事实需要照片与非空内容")
    value.setdefault("id","fact_"+uuid.uuid4().hex[:16])
    value.setdefault("subject","现场记录")
    value.setdefault("predicate","observation")
    value.setdefault("source_kind","human")
    value.setdefault("entity_id",None)
    value["status"]="confirmed"
    value["confirmed_at"]=now()
    with LOCK,database() as con:
        con.execute("INSERT INTO facts VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",(value["id"],value["photo_id"],encode(value),now()))
        rebuild_search(con,value["photo_id"])
    return value

def review_candidate(cid,state,edited_text=None):
    if state not in ("pending","confirmed","rejected"):
        raise ValueError("无效审核状态")
    with LOCK,database() as con:
        row=con.execute("SELECT * FROM candidates WHERE id=?",(cid,)).fetchone()
        if not row:
            raise KeyError("候选不存在")
        con.execute("UPDATE candidates SET state=? WHERE id=?",(state,cid))
        con.execute("DELETE FROM facts WHERE id=?",("fact_from_"+cid,))
        if state=="confirmed":
            value={"id":"fact_from_"+cid,"photo_id":row["photo_id"],"subject":"照片观察","predicate":row["kind"],"value":edited_text if edited_text is not None else row["text"],"source_kind":"candidate","candidate_id":cid,"engine":row["engine"],"status":"confirmed","confirmed_at":now()}
            con.execute("INSERT INTO facts VALUES(?,?,?,?)",(value["id"],row["photo_id"],encode(value),now()))
        rebuild_search(con,row["photo_id"])
    return {"id":cid,"state":state}

def rebuild_all_search():
    with LOCK,database() as con:
        for row in con.execute("SELECT id FROM photos").fetchall():
            rebuild_search(con,row[0])
    return {"indexed":len(list_photos())}

def search(query):
    query=query.strip()
    if not query:
        return []
    with database() as con:
        try:
            words=query.split()
            expression=" AND ".join('"'+w.replace('"','""')+'"' for w in words)
            rows=con.execute("SELECT photo_id,body FROM evidence_fts WHERE evidence_fts MATCH ? LIMIT 100",(expression,)).fetchall()
            ids={row[0] for row in rows}
            # Substring recall is necessary for unsegmented Chinese labels.
            rows+=con.execute("SELECT photo_id,body FROM evidence_fts WHERE body LIKE ? LIMIT 100",("%"+query+"%",)).fetchall()
            result=[]
            for row in rows:
                if row[0] in {r["photo_id"] for r in result}:
                    continue
                text=row[1];start=text.lower().find(query.lower())
                result.append({"photo_id":row[0],"snippet":text[max(0,start-40):max(0,start-40)+230]})
            return result[:100]
        except sqlite3.OperationalError:
            return [{"photo_id":p["id"],"snippet":encode(p["annotation"])[:230]} for p in list_photos() if query.lower() in encode(p).lower()]

def coverage_report():
    photos=list_photos();result=[]
    for f in maps()["floors"]:
        for r in f["regions"]:
            camera=[p for p in photos if p["annotation"].get("floor")==f["id"] and p["annotation"].get("camera_area")==r["id"]]
            target=[p for p in photos if any(t.get("floor")==f["id"] and (t.get("area_id")==r["id"] or r["id"] in t.get("area_candidates",[])) for t in p["annotation"].get("targets",[]))]
            result.append({"floor":f["id"],"region_id":r["id"],"room_id":r.get("room_id",r["id"]),"name":r["label"],"camera_photos":len(camera),"target_photos":len(target),"camera_photo_ids":[p["id"] for p in camera],"target_photo_ids":[p["id"] for p in target],"confirmed_positions":sum(p["annotation"].get("position_confidence")=="confirmed" for p in camera),"needs_photos":not(camera or target)})
    return result

def entity_report():
    photos=list_photos();report=[]
    for entity in entities():
        observations=[]
        for photo in photos:
            for target in photo['annotation'].get('targets',[]):
                if target.get('entity_id')==entity['id']:
                    floor=target.get('floor') or photo['annotation'].get('floor')
                    region=next((r for f in maps()['floors'] if f['id']==floor for r in f['regions'] if r['id']==target.get('area_id')),None)
                    observations.append({'photo_id':photo['id'],'floor':floor,'region_id':target.get('area_id'),'room_id':region.get('room_id') if region else None,'room_name':region.get('room_name') if region else None,'confidence':target.get('confidence','estimated'),'x_m':target.get('x_m'),'y_m':target.get('y_m')})
        confirmed=[o for o in observations if o['confidence']=='confirmed']
        rooms={o['room_id'] for o in confirmed if o['room_id']}
        conflicting=len(rooms)>1
        report.append({**entity,'observations':observations,'photo_count':len({o['photo_id'] for o in observations}),'confirmed_room_ids':sorted(rooms),'location_conflict':conflicting,'count_scope':'registered_distinct_entity_not_total_room_inventory'})
    return report

def region_distances():
    def point_segment(p,a,b):
        dx,dy=b[0]-a[0],b[1]-a[1]
        length=dx*dx+dy*dy
        t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)) if length else 0
        return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)
    def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def intersects(a,b,c,d):
        return cross(a,b,c)*cross(a,b,d)<=0 and cross(c,d,a)*cross(c,d,b)<=0 and max(min(a[0],b[0]),min(c[0],d[0]))<=min(max(a[0],b[0]),max(c[0],d[0]))+1e-9 and max(min(a[1],b[1]),min(c[1],d[1]))<=min(max(a[1],b[1]),max(c[1],d[1]))+1e-9
    def gap(a,b):
        if inside(a[0],b) or inside(b[0],a):return 0
        result=math.inf
        for i,aa in enumerate(a):
            ab=a[(i+1)%len(a)]
            for j,ba in enumerate(b):
                bb=b[(j+1)%len(b)]
                if intersects(aa,ab,ba,bb):return 0
                result=min(result,point_segment(aa,ba,bb),point_segment(ab,ba,bb),point_segment(ba,aa,ab),point_segment(bb,aa,ab))
        return result
    result=[]
    for floor in maps()['floors']:
        for i,a in enumerate(floor['regions']):
            for b in floor['regions'][i+1:]:
                ap=[metric(floor['id'],*p) for p in a['points']];bp=[metric(floor['id'],*p) for p in b['points']]
                ac=a.get('centroid_m') or metric(floor['id'],*a['at']);bc=b.get('centroid_m') or metric(floor['id'],*b['at'])
                result.append({'floor':floor['id'],'a':a['id'],'a_name':a['label'],'b':b['id'],'b_name':b['label'],'centroid_distance_m':round(math.dist(ac,bc),4),'boundary_gap_m':round(gap(ap,bp),4),'unit_status':'accepted_mm_pending_verification','measurement_basis':'nominal_plan_dimensions_and_manual_boundaries','same_parent_room':a.get('room_id')==b.get('room_id')})
    return result

def snapshot():
    with LOCK,database() as con:
        return {"schema_version":2,"created_at":now(),"maps":maps(),"photos":list_photos(),"entities":entities(),
                "relations":[dict(r) for r in con.execute("SELECT * FROM relations")],
                "candidates":[{**dict(r),"payload":json.loads(r["payload"])} for r in con.execute("SELECT * FROM candidates")],
                "facts":[json.loads(r[0]) for r in con.execute("SELECT value FROM facts")],"coverage":coverage_report(),'entity_report':entity_report()}

def restore_snapshot(value):
    if value.get("schema_version")!=2 or not isinstance(value.get("photos"),list):
        raise ValueError("需要本工具 schema_version=2 的 JSON 备份")
    available={p["id"] for p in list_photos()}
    missing=[p["id"] for p in value["photos"] if p["id"] not in available]
    if missing:
        raise ValueError(f"请先导入备份对应的图片，当前缺少 {len(missing)} 张")
    # Verify the entire import before mutating the database.
    for photo in value["photos"]:
        if photo["annotation"].get("map_version")!=maps().get("revision"):
            raise ValueError("底图版本不一致，需先迁移坐标")
    checked=[(p["id"],validate_annotation(p["annotation"])) for p in value["photos"]]
    restored_map=None
    if value.get('maps'):
        current=copy.deepcopy(maps());current.pop('calibration',None)
        incoming=value['maps']
        def geometry_signature(map_value):
            return [(f['id'],f['width'],f['height'],[(r['id'],r['points']) for r in f['regions']]) for f in map_value['floors']]
        if geometry_signature(current)!=geometry_signature(incoming):
            raise ValueError('备份的地图几何与当前版本不一致，需先迁移坐标')
        name_fields=('label','name','short_label','room_name','name_status','name_verification','aliases','site_name','site_name_confirmed_at','site_name_note')
        incoming_regions={r['id']:r for f in incoming['floors'] for r in f['regions']}
        for floor in current['floors']:
            for region in floor['regions']:
                source=incoming_regions[region['id']]
                if not source.get('label') or len(str(source['label']))>160:
                    raise ValueError('备份的房间名称无效')
                for field in name_fields:
                    if field in source:region[field]=source[field]
                    else:region.pop(field,None)
        current['name_revision']=incoming.get('name_revision',0)
        restored_map=current
    # Backup is mandatory and automatic before merge restore.
    backup_database()
    map_path=ROOT/'assets/map-regions.json'
    old_map_bytes=map_path.read_bytes()
    map_replaced=False
    try:
        with LOCK,database() as con:
            con.execute("BEGIN IMMEDIATE")
            imported_ids=[pid for pid,_ in checked]
            for pid in imported_ids:
                con.execute("DELETE FROM facts WHERE photo_id=?",(pid,))
                con.execute("DELETE FROM candidates WHERE photo_id=?",(pid,))
                con.execute("DELETE FROM relations WHERE photo_id=? OR other_id=?",(pid,pid))
            for pid,a in checked:
                old=con.execute("SELECT value,revision FROM annotations WHERE photo_id=?",(pid,)).fetchone()
                revision=old["revision"] if old else 0
                if old:
                    con.execute("INSERT INTO revisions(photo_id,value,revision,created_at) VALUES(?,?,?,?)",(pid,old["value"],revision,now()))
                a.update(revision=revision+1,updated_at=now())
                con.execute("INSERT INTO annotations VALUES(?,?,?,?) ON CONFLICT(photo_id) DO UPDATE SET value=excluded.value,revision=excluded.revision,updated_at=excluded.updated_at",(pid,encode(a),a["revision"],a["updated_at"]))
            for e in value.get("entities",[]):
                if not e.get("id") or not e.get("name"):
                    raise ValueError("备份中的实体缺少编号或名称")
                con.execute("INSERT INTO entities VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(e["id"],encode(e),now()))
            for r in value.get("relations",[]):
                con.execute("INSERT INTO relations VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET note=excluded.note",(r["id"],r["photo_id"],r["other_id"],r["kind"],r["note"],r["created_at"]))
            for c in value.get("candidates",[]):
                con.execute("INSERT OR IGNORE INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?)",(c["id"],c["photo_id"],c["kind"],c["engine"],c["text"],encode(c["payload"]),c["raw"],c["state"],c.get("cache_key"),c["created_at"]))
            for fact in value.get("facts",[]):
                con.execute("INSERT INTO facts VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",(fact["id"],fact["photo_id"],encode(fact),now()))
            if restored_map:
                (DATA/'backups'/f"room-names-before-restore-{uuid.uuid4().hex[:12]}.json").write_bytes(old_map_bytes)
                temp=map_path.with_suffix('.json.tmp');temp.write_text(json.dumps(restored_map,ensure_ascii=False,indent=2),encoding='utf8');os.replace(temp,map_path);map_replaced=True
    except Exception:
        if map_replaced:
            temp=map_path.with_suffix('.json.tmp');temp.write_bytes(old_map_bytes);os.replace(temp,map_path)
        raise
    rebuild_all_search()
    return {"restored":len(checked)}

def backup_database():
    folder=DATA/"backups";folder.mkdir(exist_ok=True)
    path=folder/f"workbench-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.sqlite3"
    with LOCK,database() as source,sqlite3.connect(path,factory=ClosingConnection) as dest:
        source.backup(dest)
    return path

def export_zip():
    value=snapshot();buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("annotations.json",json.dumps(value,ensure_ascii=False,indent=2))
        z.writestr("map-regions.json",json.dumps(value["maps"],ensure_ascii=False,indent=2))
        rows=io.StringIO();writer=csv.writer(rows)
        writer.writerow(["photo_id","path","floor","area_name","camera_x_m","camera_y_m","heading_deg","position_confidence","status","captured_at","notes"])
        images=[];descriptions={};chunks=[]
        region_names={r["id"]:r["label"] for floor in value["maps"]["floors"] for r in floor["regions"]}
        for p in value["photos"]:
            a=p["annotation"];cam=a.get("camera") or {}
            notes="\n".join(f"{k}: {v}" for k,v in a.get("notes",{}).items() if v)
            writer.writerow([p["id"],p["relative_path"],a.get("floor"),region_names.get(a.get("camera_area")),cam.get("x_m"),cam.get("y_m"),a.get("heading_deg"),a.get("position_confidence"),a.get("status"),a.get("captured_at"),notes])
            caption="\n".join(filter(None,[p.get('caption',''),notes]))
            record={"src":p.get("src"),"local_path":p["relative_path"],"page":p.get("page") or p.get("src") or p["relative_path"],"alt":p.get('alt',''),"caption":caption,"source_type":p["source_type"],"source_origin":p["source_origin"],"image_id":p["id"],"area":region_names.get(a.get("camera_area")),"floor":a.get("floor"),"annotation":a,"content_sha256":p["content_sha256"]}
            images.append(record)
            facts=[f for f in value["facts"] if f["photo_id"]==p["id"]]
            geometry=[]
            if cam:
                geometry.append(f"Camera location: floor {a.get('floor')}, {region_names.get(a.get('camera_area'),'area unknown')}, drawing coordinates ({cam.get('x_m')}, {cam.get('y_m')}) m; location confidence: {a.get('position_confidence')}; units accepted as millimetres pending verification, not surveyed.")
            if a.get('heading_deg') is not None:
                geometry.append(f"Camera heading: {a['heading_deg']} degrees clockwise from plan-up; heading confidence: {a.get('heading_confidence')}. Plan-up is not confirmed geographic north.")
            for target in a.get('targets',[]):
                entity=next((e for e in value['entities'] if e['id']==target.get('entity_id')),None)
                target_name=entity['name'] if entity else target.get('name','unnamed target')
                aliases=' / '.join(entity.get('aliases',[])) if entity else ''
                geometry.append(f"Target: {target_name}{' / '+aliases if aliases else ''}; floor {target.get('floor')}; area {region_names.get(target.get('area_id'),'unknown')}; drawing position {target.get('points_m') if target.get('type')=='area' else (target.get('x_m'),target.get('y_m'))} m; confidence {target.get('confidence','estimated')}; entity ID {target.get('entity_id') or 'not assigned'}; note {target.get('note','')}.")
            for link in value['relations']:
                if link['photo_id']==p['id'] or link['other_id']==p['id']:
                    other=link['other_id'] if link['photo_id']==p['id'] else link['photo_id']
                    geometry.append(f"Cross-photo relation {link['kind']} with photo {other}: {link['note']}.")
            text="\n".join([region_names.get(a.get("camera_area"),""),p.get('alt',''),caption]+geometry+[f["value"] for f in facts]).strip()
            if text:
                descriptions[p["relative_path"]]=text
                metadata={"url":record["page"],"image":p["relative_path"],"image_id":p["id"],"kind":"image","source_type":p["source_type"],"floor":a.get("floor") or "unknown","area":record["area"] or "unknown","map_version":a.get("map_version") or "","position_confidence":a.get("position_confidence") or "unknown","unit_status":"accepted_mm_pending_verification"}
                if cam:metadata.update(camera_x_m=cam.get('x_m'),camera_y_m=cam.get('y_m'))
                if a.get('heading_deg') is not None:metadata['heading_deg']=a['heading_deg']
                chunks.append({"id":"spatial_"+p["id"],"text":text,"metadata":metadata})
        z.writestr("annotations.csv","\ufeff"+rows.getvalue())
        z.writestr("images.json",json.dumps(images,ensure_ascii=False,indent=2))
        z.writestr("descriptions.json",json.dumps(descriptions,ensure_ascii=False,indent=2))
        z.writestr("spatial-chunks.json",json.dumps(chunks,ensure_ascii=False,indent=2))
        z.writestr("confirmed-facts.json",json.dumps(value["facts"],ensure_ascii=False,indent=2))
        z.writestr("coverage.json",json.dumps(value["coverage"],ensure_ascii=False,indent=2))
        z.writestr("entity-report.json",json.dumps(value["entity_report"],ensure_ascii=False,indent=2))
        z.writestr("region-distances.json",json.dumps(region_distances(),ensure_ascii=False,indent=2))
        z.writestr("map-calibration.json",json.dumps(value['maps']['calibration'],ensure_ascii=False,indent=2))
        z.writestr("README.txt","UTF-8. JSON preserves original OCR/model candidates separately from confirmed facts. Image paths are relative to the workbench root. The competition main image builder needs the supplied adapter for local paths and spatial metadata; no repository files are modified by this export. Units are accepted millimetres pending verification; coordinates are drawing-based, not surveyed.\n")
    return buffer.getvalue()
