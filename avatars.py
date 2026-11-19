"""Resolve and cache a small PNG using the blog's own favicon or logo."""
import hashlib
import os
import re
import subprocess
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib import parse, request

import services


class IconLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.base = ''
        self.icons = []
        self.logos = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get('href')
        if tag == 'base' and href and not self.base:
            self.base = href
        if tag == 'link' and href:
            rel = (attrs.get('rel') or '').lower().split()
            if 'icon' in rel or 'apple-touch-icon' in rel or 'apple-touch-icon-precomposed' in rel:
                self.icons.append(href)
        if tag == 'img' and attrs.get('src'):
            clue = ' '.join(attrs.get(k) or '' for k in ('src','class','alt','id'))
            if re.search(r'logo|logomark',clue,re.I):
                self.logos.append(attrs['src'])


def get(url, limit, *, partial=False):
    parts = parse.urlsplit(url)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Invalid icon URL')
    req = request.Request(url,headers={'User-Agent':'Foliofinch/1.0','Accept-Encoding':'identity'})
    with request.urlopen(req,timeout=6) as response:
        buf = response.read(limit+1)
        if not partial and len(buf)>limit:
            raise ValueError('Oversize icon')
        return response.geturl(),buf[:limit]


def cached_file(feed):
    url = feed.get('website') or feed['url']
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    return services.DATA/'avatars'/(key+'.png')


def candidates(site):
    page = IconLinks()
    base = site
    try:
        base,html = get(site,512_000,partial=True)
        page.feed(html.decode('utf-8',errors='replace'))
        base = parse.urljoin(base,page.base)
    except (OSError,ValueError):
        pass
    hrefs = page.icons + ['/favicon.ico'] + page.logos[:2] + ['/apple-touch-icon.png']
    return list(dict.fromkeys(parse.urljoin(base,h) for h in hrefs))


def freshrss_copy(site):
    for entry in (services.FRESH/'data/favicons').glob('*.txt'):
        if entry.read_text(errors='replace').strip()==site and entry.with_suffix('.ico').is_file():
            return entry.with_suffix('.ico').read_bytes()
    return None


def resolve(feed):
    site = feed.get('website') or feed['url']
    path = cached_file(feed)
    path.parent.mkdir(exist_ok=True)
    attempted = path.with_suffix('.checked')
    now = time.time()
    if path.exists() and now-path.stat().st_mtime < 7*86400:
        return str(path)
    if attempted.exists() and now-attempted.stat().st_mtime < 12*3600:
        return str(path) if path.exists() else None
    with tempfile.TemporaryDirectory(dir=path.parent) as folder:
        source = Path(folder)/'icon'
        result = Path(folder)/'thumb.png'
        for url in [*candidates(site),None]:
            try:
                data = get(url,2_000_000)[1] if url else freshrss_copy(site)
                if not data or re.search(br'<(?:html|!doctype html)\b',data[:500],re.I):
                    continue
                source.write_bytes(data)
                result.unlink(missing_ok=True)
                subprocess.run(['/usr/bin/sips','-s','format','png','-Z','96',str(source),'--out',str(result)],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=8)
                if result.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
                    os.replace(result,path)
                    break
            except (ValueError,OSError,subprocess.SubprocessError):
                continue
    attempted.touch()
    return str(path) if path.exists() else None
