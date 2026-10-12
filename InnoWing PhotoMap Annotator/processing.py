"""Offline OCR/Vision candidates with provenance, review and resumable caches."""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import threading
import urllib.request
import uuid
from pathlib import Path
import workbench as w

PROMPT_VERSION="spatial-v2-2026-10-12"
PROMPT="""Extract searchable visual evidence about the Tam Wing Fan Innovation Wing.
Return JSON only with keys: text (all legible words verbatim, preserving spelling),
description (visible factual summary), objects (list of {name,count,count_scope,
colour,material,location_in_image,uncertainty}), relations (visible object-to-object
relations), uncertainties (list). Use count_scope='visible_only' or 'at_least'.
Do not infer global room counts, true coordinates, compass directions or hidden
objects from a photo. Do not treat provided location notes as independently
verified visual facts. Unreadable text is [unreadable]. No invented text.
All output is a candidate requiring human confirmation."""
RUNTIME={"api_key":os.getenv("AZURE_OPENAI_KEY",os.getenv("OPENAI_API_KEY","")).strip()}
ACTIVE_LOCK=threading.Lock()

def config():
    base=os.getenv("AZURE_CHAT_BASE","https://api-iw.azure-api.net/sig-shared-jpeast-increased")
    model=os.getenv("VISION_DEPLOYMENT","gpt-5-mini")
    defaults={"vision_url":os.getenv("VISION_URL",f"{base}/deployments/{model}/chat/completions?api-version={os.getenv('AZURE_OPENAI_API_VERSION','2025-01-01-preview')}"),"vision_model":model,"auth_mode":"azure","ocr_language":"en-US","max_completion_tokens":1600}
    with w.database() as con:
        row=con.execute("SELECT value FROM settings WHERE key='processing'").fetchone()
    defaults.update(json.loads(row[0]) if row else {})
    defaults["key_configured"]=bool(RUNTIME["api_key"])
    defaults["local_ocr_available"]=os.name=="nt" or bool(shutil.which("tesseract"))
    return defaults

def save_config(value):
    if "api_key" in value and value["api_key"]:
        RUNTIME["api_key"]=value.pop("api_key").strip()
    else:
        value.pop("api_key",None)
    allowed={"vision_url","vision_model","auth_mode","ocr_language","max_completion_tokens"}
    current=config();current={k:v for k,v in current.items() if k in allowed}
    current.update({k:v for k,v in value.items() if k in allowed})
    if not str(current["vision_url"]).startswith("https://"):
        raise ValueError("视觉识别端点需要 HTTPS URL")
    if current["auth_mode"] not in ("azure","bearer"):
        raise ValueError("认证方式无效")
    with w.LOCK,w.database() as con:
        con.execute("INSERT INTO settings VALUES('processing',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(w.encode(current),))
    return config()

def get_jobs():
    with w.database() as con:
        return [json.loads(row[0]) for row in con.execute("SELECT value FROM jobs ORDER BY id DESC LIMIT 20")]

def set_job(value):
    with w.LOCK,w.database() as con:
        con.execute("INSERT INTO jobs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",(value["id"],w.encode(value)))

def local_ocr(image_path,language):
    if os.name=="nt":
        command=[str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'),"-NoProfile","-ExecutionPolicy","Bypass","-File",str(w.ROOT/'scripts/windows_ocr.ps1'),"-ImagePath",str(image_path),"-Language",language]
        result=subprocess.run(command,capture_output=True,encoding="utf-8",errors="replace",timeout=90,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if result.returncode:
            raise RuntimeError("Windows OCR: "+result.stderr.strip()[:500])
        return json.loads(result.stdout.lstrip("\ufeff").strip())
    engine=shutil.which("tesseract")
    if not engine:
        raise RuntimeError("本地 OCR 引擎不可用；可安装 Tesseract，导入 OCR 文本或使用视觉识别。")
    result=subprocess.run([engine,str(image_path),"stdout"],capture_output=True,text=True,timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr[:400])
    return {"text":result.stdout,"lines":[],"language":"tesseract default"}

def image_crops(photo,tiles=False):
    from PIL import Image,ImageOps
    im=ImageOps.exif_transpose(Image.open(w.photo_path(photo["id"]))).convert("RGB")
    crops=[("whole",im,(0,0,im.width,im.height))]
    for box in photo["annotation"].get("boxes",[]):
        coords=(int(box["x"]*im.width),int(box["y"]*im.height),int((box["x"]+box["width"])*im.width),int((box["y"]+box["height"])*im.height))
        if coords[2]>coords[0] and coords[3]>coords[1]:
            crops.append(("box:"+box["id"],im.crop(coords),coords))
    if tiles and max(im.size)>2500:
        for y in range(0,im.height,1536):
            for x in range(0,im.width,1536):
                if len(crops)>=13:
                    break
                coords=(x,y,min(im.width,x+1792),min(im.height,y+1792))
                crops.append((f"tile:{x}:{y}",im.crop(coords),coords))
    return crops

def vision(crop,configuration):
    if not RUNTIME["api_key"]:
        raise RuntimeError("视觉识别尚未配置密钥；可在设置中填写或设置 AZURE_OPENAI_KEY 环境变量。")
    crop.thumbnail((2048,2048))
    buffer=io.BytesIO();crop.save(buffer,"JPEG",quality=90)
    payload={"model":configuration["vision_model"],"messages":[{"role":"user","content":[{"type":"text","text":PROMPT},{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+base64.b64encode(buffer.getvalue()).decode(),"detail":"high"}}]}],"max_completion_tokens":int(configuration["max_completion_tokens"])}
    headers={"Content-Type":"application/json"}
    if configuration["auth_mode"]=="azure":
        headers["api-key"]=RUNTIME["api_key"]
    else:
        headers["Authorization"]="Bearer "+RUNTIME["api_key"]
    request=urllib.request.Request(configuration["vision_url"],data=json.dumps(payload).encode(),headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=100) as response:
            result=json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Never return credential-bearing headers or uncontrolled provider responses.
        raise RuntimeError(f"视觉识别 HTTP {exc.code}；请核对端点、认证方式与 deployment。") from None
    raw=result["choices"][0]["message"]["content"]
    clean=raw.strip()
    if clean.startswith("```"):
        clean=clean.split("\n",1)[1].rsplit("```",1)[0]
    try:
        parsed=json.loads(clean)
        if not isinstance(parsed,dict):
            raise ValueError()
    except (ValueError,TypeError):
        parsed={"description":raw,"objects":[],"uncertainties":["模型未输出有效 JSON，需人工检查原文。"]}
    return parsed,raw

def process_one(pid,mode,tiles=False):
    photo=w.get_photo(pid);configuration=config();created=[]
    for label,crop,coords in image_crops(photo,tiles):
        key=hashlib.sha256(w.encode({"hash":photo["content_sha256"],"crop":coords,"mode":mode,"prompt":PROMPT_VERSION,"engine":configuration["vision_model"] if mode=="vision" else "windows-ocr" if os.name=="nt" else "tesseract","language":configuration["ocr_language"],"endpoint":configuration["vision_url"] if mode=="vision" else None}).encode()).hexdigest()
        with w.database() as con:
            cached=con.execute("SELECT id FROM candidates WHERE cache_key=?",(key,)).fetchone()
        if cached:
            created.append(cached[0]);continue
        if mode=="ocr":
            scale=min(1,2048/max(crop.size));crop.thumbnail((2048,2048))
            folder=w.DATA/'cache/ocr';folder.mkdir(parents=True,exist_ok=True)
            path=folder/(key+'.png');crop.save(path)
            result=local_ocr(path,configuration["ocr_language"])
            payload={"crop":coords,"crop_label":label,"resize_scale":scale,"ocr":result,"coordinate_space":"resized crop pixels","uncertainty":"OCR 候选未经人工核验"}
            text=result.get("text","") or "[未识别到可读文字]"
            cid=w.add_candidate(pid,"ocr","Windows OCR" if os.name=="nt" else "Tesseract",text,payload,result,key)
        elif mode=="vision":
            parsed,raw=vision(crop,configuration)
            text="\n".join(str(parsed.get(k,"")) for k in ("text","description","objects","relations","uncertainties") if parsed.get(k))
            cid=w.add_candidate(pid,"vision",configuration["vision_model"],text,{"crop":coords,"crop_label":label,"result":parsed,"prompt_version":PROMPT_VERSION},raw,key)
        else:
            raise ValueError("处理方式仅支持 ocr 或 vision")
        created.append(cid)
    return {"photo_id":pid,"candidate_ids":created}

def start_job(ids,mode,tiles=False):
    if mode not in ("ocr","vision","index"):
        raise ValueError("任务类型无效")
    if mode=="vision" and not RUNTIME["api_key"]:
        raise ValueError("先配置视觉识别密钥；本地 OCR、标注和检索无需密钥。")
    if not ACTIVE_LOCK.acquire(blocking=False):
        raise RuntimeError("已有处理任务运行，请等待完成或停止当前任务")
    job={"id":"job_"+w.now()+"_"+uuid.uuid4().hex[:6],"state":"queued","mode":mode,"total":len(ids),"processed":0,"success":0,"failures":[],"created_at":w.now(),"tiles":bool(tiles),"message":"准备处理"}
    set_job(job)
    def worker():
        try:
            job["state"]="running";set_job(job)
            if mode=="index":
                w.rebuild_all_search();job.update(processed=len(ids),success=len(ids))
            else:
                for pid in ids:
                    current=next((j for j in get_jobs() if j["id"]==job["id"]),{})
                    if current.get("state")=="cancelling":
                        job.update(state="cancelled",message="任务已停止，已完成结果与缓存保留");set_job(job);return
                    try:
                        process_one(pid,mode,tiles);job["success"]+=1
                    except Exception as exc:
                        job["failures"].append({"photo_id":pid,"error":str(exc)[:600]})
                    job["processed"]+=1;job["message"]=f"已处理 {job['processed']} / {job['total']}";set_job(job)
            job.update(state="completed",finished_at=w.now(),message="处理完成；候选仍需人工审核")
            set_job(job)
        except Exception as exc:
            job.update(state="failed",message=str(exc)[:500]);set_job(job)
        finally:
            ACTIVE_LOCK.release()
    threading.Thread(target=worker,daemon=True).start()
    return job

def cancel_job(jid):
    job=next((j for j in get_jobs() if j["id"]==jid),None)
    if not job:
        raise KeyError("任务不存在")
    if job["state"] in ("queued","running"):
        job.update(state="cancelling",message="当前图片完成后停止");set_job(job)
    return job
