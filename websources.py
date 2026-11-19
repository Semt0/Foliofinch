"""Track the article links visible on a public HTML index, independently of RSS."""
from datetime import datetime
from fnmatch import fnmatchcase
from html.parser import HTMLParser
import re
from urllib import parse, request

import inbox


def fetch(url):
    req = request.Request(url, headers={'User-Agent':'BlogDaily/1.0','Accept-Encoding':'identity'})
    with request.urlopen(req, timeout=20) as response:
        raw = response.read(5_000_001)
        if len(raw) > 5_000_000 and not re.search(br'<(?:rss|feed|rdf:RDF)(?:\s|>)',raw[:12000],re.I) and not raw.lstrip().startswith(b'{'):
            raise ValueError('页面超过 5 MB，试试更精简的文章列表地址')
        charset = response.headers.get_content_charset()
        if not charset:
            match = re.search(br'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
            charset = match.group(1).decode() if match else 'utf-8'
        return response.geturl(), raw.decode(charset, errors='replace')


class Page(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.stack = []
        self.links = []
        self.title = []
        self.base = ''
        self.published = ''
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'base' and not self.base:
            self.base = attrs.get('href') or ''
        if tag == 'meta' and (attrs.get('property') or attrs.get('name')) in ('article:published_time','datePublished'):
            self.published = attrs.get('content') or ''
        if tag in inbox.Text.void:
            return
        # HTML permits omitted closing tags on list items.
        if tag == 'a' and any(t == 'a' for t, _ in self.stack):
            self.handle_endtag('a')
        parents = [t for t, _ in self.stack]
        link = self.stack[-1][1] if self.stack else None
        if tag == 'a':
            link = None
            if attrs.get('href') and not set(parents).intersection({'nav','footer','aside','template','script'}):
                link = dict(href=attrs['href'], text=[], heading=[], fallback=attrs.get('aria-label') or attrs.get('title') or '',
                            context=set(parents))
                self.links.append(link)
        self.stack.append((tag,link))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        tags = {t for t,_ in self.stack}
        if 'title' in tags and 'head' in tags:
            self.title.append(data)
        if self.stack and self.stack[-1][1] and not tags.intersection({'svg','style','script'}):
            link = self.stack[-1][1]
            link['text'].append(data)
            if tags.intersection({'h1','h2','h3','h4'}):
                link['heading'].append(data)


def listing(url, pattern='', page=None):
    base, markup = page if page is not None else fetch(url)
    doc = Page(markup)
    location = parse.urlsplit(base)
    root = parse.urljoin(base, doc.base)
    entries = {}
    cards = set()
    editorial = set()
    has_main = any('main' in link['context'] for link in doc.links)
    for link in doc.links:
        if not pattern and has_main and 'main' not in link['context']:
            continue
        href = link['href'].strip()
        if href.startswith(('#','?')) and not pattern:
            continue
        dest = parse.urljoin(root, href)
        try:
            key = inbox.canonical(dest)
        except ValueError:
            continue
        path = parse.urlsplit(key)
        if path.netloc.lower() != location.netloc.lower() or key == inbox.canonical(base):
            continue
        if re.search(r'\.(?:jpg|png|gif|pdf|zip|xml|svg|webp|mp4|json)$',path.path,re.I):
            continue
        title = ' '.join((''.join(link['heading'] or link['text']) or link['fallback']).split())
        if len(title)<2 or title.casefold() in {'read more','learn more','more','阅读全文','查看全部','home','首页'}:
            continue
        route = path.path + ('?' + path.query if path.query else '')
        if pattern:
            if not fnmatchcase(route, pattern):
                continue
        else:
            if re.search(r'/(?:tags?|categor(?:y|ies)|authors?|topics?|page|about|contact|careers|subscribe)(/|$)',path.path,re.I):
                continue
            structural = bool(link['heading'] or link['context'].intersection({'h1','h2','h3','h4','article','li'}))
            article_path = re.search(r'/(?:posts?|blog|research|articles?|news|engineering|notes|20\d\d)/[^/]',path.path,re.I)
            if not (structural or article_path) or len(title)<4:
                continue
        if 'article' in link['context']:
            cards.add(key)
        if link['heading'] or link['context'].intersection({'article','h2','h3','h4'}):
            editorial.add(key)
        entries.setdefault(key, {'url':parse.urldefrag(dest).url, 'title':title[:350]})
    if len(cards) >= 3:
        editorial = cards
    if not pattern and len(editorial) >= 3:
        entries = {k:v for k,v in entries.items() if k in editorial}
    if not entries:
        raise ValueError('没有识别到文章链接。请使用公开的文章列表页，或调整下方的文章路径规则。')
    name = ' '.join(''.join(doc.title).split()) or location.hostname
    return {'kind':'web','url':base,'name':name,'items':list(entries.values())}


def register(url, pattern):
    preview = listing(url,pattern)
    sid = 'feed/web-' + inbox.digest(inbox.canonical(preview['url']))[:20]
    with inbox.database() as db:
        existing = db.execute('SELECT id FROM web_sources WHERE id=?',(sid,)).fetchone()
        if existing:
            return '已经订阅该网页'
        db.execute('INSERT INTO web_sources(id,url,name,pattern) VALUES(?,?,?,?)',
                   (sid,preview['url'],preview['name'],pattern))
        enqueue(db,sid,preview['items'],initial=True)
    return '已添加 '+preview['name']


def enqueue(db, sid, items, initial=False):
    day = '' if initial else datetime.now().date().isoformat()
    for i,item in enumerate(items):
        db.execute('''INSERT OR IGNORE INTO web_links(source,key,url,title,day,pending)
                      VALUES(?,?,?,?,?,?)''',
                   (sid,inbox.canonical(item['url']),item['url'],item['title'],day,int(not initial or i<3)))


def sources():
    with inbox.database() as db:
        return [dict(id=r['id'],name=r['name'],url=r['url'],failed=bool(r['error']))
                for r in db.execute('SELECT * FROM web_sources ORDER BY name')]


def remove(sid):
    with inbox.database() as db:
        db.execute('DELETE FROM web_sources WHERE id=?',(sid,))
        db.execute('DELETE FROM web_links WHERE source=?',(sid,))
        db.execute('DELETE FROM article_feeds WHERE feed=?',(sid,))


def collect(source, link):
    aid = inbox.digest(link['key'])[:20]
    with inbox.database() as db:
        existing = db.execute('SELECT body FROM articles WHERE id=?',(aid,)).fetchone()
    if not existing or not existing['body']:
        _, markup = fetch(link['url'])
        for tag in ('article','main','p'):
            paragraphs = inbox.Text(tag, paragraphs_only=True).extract(markup).splitlines()
            body = '\n'.join(p for p in paragraphs if len(p) >= 20)
            if len(body) >= 80:
                break
        else:
            body = inbox.Text('article').extract(markup)
            if len(body) < 80:
                raise ValueError('未取到足够的文章正文')
        published = 0
        try:
            stamp = Page(markup).published
            if stamp:
                published = int(datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp())
        except (ValueError,OverflowError):
            pass
        with inbox.database() as db:
            db.execute("""INSERT INTO articles(id,url,title,source,published,body,feed_hash,coverage,discovered)
                      VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,
                      coverage=excluded.coverage,problem='' WHERE articles.body=''""",
                       (aid,link['url'],link['title'],source['name'],published,body,inbox.digest(body),'网页正文',link['day']))
    with inbox.database() as db:
        db.execute('INSERT OR IGNORE INTO article_feeds VALUES(?,?)',(aid,source['id']))
        db.execute('UPDATE web_links SET pending=0 WHERE source=? AND key=?',(source['id'],link['key']))


def refresh(notify):
    errors = []
    with inbox.database() as db:
        subscriptions = list(db.execute('SELECT * FROM web_sources'))
    for source in subscriptions:
        sid = source['id']
        notify('正在检查 '+source['name'])
        problems = []
        try:
            items = listing(source['url'],source['pattern'])['items']
            with inbox.database() as db:
                enqueue(db,sid,items)
        except Exception as exc:
            problems.append(str(exc))
        with inbox.database() as db:
            pending = list(db.execute('SELECT * FROM web_links WHERE source=? AND pending=1 ORDER BY rowid',(sid,)))
        for link in pending:
            try:
                collect(source,link)
            except Exception as exc:
                problems.append(f"{link['title']}：{exc}")
        problem = '；'.join(problems)
        with inbox.database() as db:
            db.execute('UPDATE web_sources SET error=? WHERE id=?',(problem,sid))
        if problem:
            errors.append(source['name']+'：'+problem)
    return errors
