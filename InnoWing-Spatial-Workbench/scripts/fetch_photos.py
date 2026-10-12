"""Fetch only the two requested public photo collections, with resumable verification.

No vision API is used. Original bytes and folder names are preserved. A manifest
row is retained per source even if multiple sources contain identical bytes.
Run: python scripts/fetch_photos.py [--workers 6] [--verify-only]
"""
import argparse
import concurrent.futures
import hashlib
import io
import json
import re
import struct
import sys
import time
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
REPO = 'jingtongxu9-web/AI-chatbot-competition'
ESTATES = 'https://www.estates.hku.hk/project-management/recently-completed-projects/tam-wing-fan-innovation-wing'
MANIFEST = ROOT / 'assets/photo-manifest.json'
SOURCES = ROOT / 'assets/photo-sources.json'
MAX_BYTES = 100 * 1024 * 1024


def fetch(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 InnoWing-Spatial-Workbench/1.0'})
            with urllib.request.urlopen(req, timeout=50) as response:
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError('File exceeds the 100 MB safety limit')
                return data
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1 + attempt * 2)


def tree_payload(html):
    for match in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.S):
        try:
            payload = json.loads(match.group(1))['payload']['codeViewTreeRoute']
            if 'tree' in payload:
                return payload
        except (ValueError, KeyError, TypeError):
            pass
    raise ValueError('GitHub directory listing absent')


class Gallery(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('data-fancybox-group') == 'easy-gallery-13006':
            src = urljoin(ESTATES, attrs['href'])
            self.items[src] = attrs.get('title', '').strip()


def discover():
    # Pin main before walking folders, so a branch update cannot mix revisions.
    root_url = f'https://github.com/{REPO}/tree/main/supplementary%20images'
    payload = tree_payload(fetch(root_url).decode('utf8'))
    pin = payload['refInfo']['currentOid']
    gallery = Gallery()
    gallery.feed(fetch(ESTATES).decode('utf8'))
    items = []
    names = set()
    for raw_src, caption in gallery.items.items():
        src = quote(raw_src, safe=':/?=&%')
        name = unquote(Path(urlparse(raw_src).path).name)
        if name in names:
            name = hashlib.sha256(src.encode()).hexdigest()[:8] + '-' + name
        names.add(name)
        items.append(dict(relative_path='assets/photos/official/' + name,
                          folder_hint='Official project gallery', source_origin='hku_estates',
                          source_type='website', src=src, page=ESTATES, caption=caption))
    pending = [x['path'] for x in payload['tree']['items'] if x['contentType'] == 'directory']
    folders = {}
    while pending:
        folder = pending.pop(0)
        children = tree_payload(fetch(f'https://github.com/{REPO}/tree/{pin}/{quote(folder)}').decode('utf8'))['tree']['items']
        count = 0
        for child in children:
            if child['contentType'] == 'directory':
                pending.append(child['path'])
            elif Path(child['name']).suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp'):
                relative = child['path'].removeprefix('supplementary images/')
                items.append(dict(relative_path='assets/photos/team/' + relative,
                                  folder_hint=folder.removeprefix('supplementary images/'),
                                  source_origin='team_github', source_type='physical',
                                  src=f'https://raw.githubusercontent.com/{REPO}/{pin}/{quote(child["path"])}',
                                  page=f'https://github.com/{REPO}/blob/{pin}/{quote(child["path"])}',
                                  git_blob_sha=child.get('oid')))
                count += 1
        folders[folder] = count
        print('Listed folder:', folder, count, flush=True)
    return dict(version=1, repository=REPO, repository_commit=pin, folders=folders, images=items)


def metadata(data):
    captured = None
    orientation = None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
            exif = im.getexif()
            orientation = exif.get(274)
            original = exif.get(36867) or exif.get(306)
            if hasattr(exif, 'get_ifd'):
                original = exif.get_ifd(34665).get(36867) or original
            if original:
                original = str(original).strip('\x00 ')
                if re.fullmatch(r'\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}', original):
                    captured = original[:10].replace(':', '-') + 'T' + original[11:]
                else:
                    captured = original
            return width, height, captured, orientation
    except ImportError:
        pass
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        width, height = struct.unpack('>II', data[16:24])
        return width, height, captured, orientation
    if data.startswith(b'\xff\xd8'):
        i = 2
        while i < len(data):
            if data[i] != 255:
                i += 1
                continue
            while i < len(data) and data[i] == 255:
                i += 1
            marker = data[i]
            i += 1
            if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
                continue
            if marker == 0xda:
                break
            length = struct.unpack('>H', data[i:i + 2])[0]
            if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
                height, width = struct.unpack('>HH', data[i + 3:i + 7])
                return width, height, captured, orientation
            i += length
    raise ValueError('Unrecognized or invalid image bytes; install Pillow for other formats')


def download(item, previous, verify_only):
    item = dict(item)
    path = ROOT / item['relative_path']
    if not path.resolve().is_relative_to(ROOT.resolve() / 'assets/photos'):
        return None, dict(**item, error='Path outside assets/photos')
    try:
        data = path.read_bytes() if path.exists() else None
        old_hash = previous.get(item['relative_path'], {}).get('content_sha256')
        if data is not None and old_hash and hashlib.sha256(data).hexdigest() != old_hash:
            if verify_only:
                raise ValueError('Saved content SHA-256 differs from manifest')
            data = None
        if data is None:
            if verify_only:
                raise FileNotFoundError('Photo is not downloaded')
            data = fetch(item['src'])
        width, height, captured, orientation = metadata(data)
        sha = hashlib.sha256(data).hexdigest()
        blob_sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        expected_blob = item.get('git_blob_sha')
        if expected_blob and expected_blob != blob_sha:
            raise ValueError('Downloaded bytes do not match pinned Git blob SHA')
        if not path.exists() or (old_hash and old_hash != sha):
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.part')
            temporary.write_bytes(data)
            temporary.replace(path)
        item.update(id=sha[:24], content_sha256=sha, width=width, height=height,
                    captured_at=captured, captured_at_source='exif' if captured else None,
                    exif_orientation=orientation, bytes=len(data))
        return item, None
    except Exception as exc:
        return None, dict(**item, error=f'{type(exc).__name__}: {exc}')


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    tmp.replace(path)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8', errors='replace')
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--verify-only', action='store_true')
    ap.add_argument('--rediscover', action='store_true')
    args = ap.parse_args()
    sources = json.loads(SOURCES.read_text(encoding='utf8')) if SOURCES.exists() and not args.rediscover else discover()
    save_json(SOURCES, sources)
    previous = {}
    if MANIFEST.exists():
        previous = {x['relative_path']: x for x in json.loads(MANIFEST.read_text(encoding='utf8')).get('images', [])}
    manifest = dict(version=1, repository=REPO, repository_commit=sources['repository_commit'],
                    retrieved_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    folders=sources.get('folders', {}), images=[], failures=[])
    print('Pinned repository:', sources['repository_commit'], flush=True)
    print('Photo sources:', len(sources['images']), flush=True)
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 12))) as pool:
        pending = [pool.submit(download, x, previous, args.verify_only) for x in sources['images']]
        for future in concurrent.futures.as_completed(pending):
            item, failure = future.result()
            if item:
                manifest['images'].append(item)
            if failure:
                manifest['failures'].append(failure)
                print('Failed:', failure['relative_path'], failure['error'], flush=True)
            completed += 1
            if completed % 10 == 0 or completed == len(pending):
                manifest['images'].sort(key=lambda x: x['relative_path'])
                save_json(MANIFEST, manifest)
                print('Completed:', completed, '/', len(pending), flush=True)
    manifest['images'].sort(key=lambda x: x['relative_path'])
    counts = Counter(x['source_origin'] for x in manifest['images'])
    hashes = Counter(x['content_sha256'] for x in manifest['images'])
    manifest['summary'] = dict(downloaded=len(manifest['images']), failures=len(manifest['failures']),
                               bytes=sum(x['bytes'] for x in manifest['images']),
                               by_origin=dict(counts), unique_contents=len(hashes),
                               duplicate_source_rows=sum(v - 1 for v in hashes.values()))
    save_json(MANIFEST, manifest)
    print(json.dumps(manifest['summary']), flush=True)
    return 1 if manifest['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
