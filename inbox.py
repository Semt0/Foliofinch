import calendar
import contextlib
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from urllib import parse, request

from services import DATA, MODEL_URL, local, php

DB = DATA / 'inbox.sqlite'
SOURCES = {'bair.berkeley.edu': 'BAIR', 'lilianweng.github.io': 'Lil’Log'}


@contextlib.contextmanager
def database():
    with contextlib.closing(sqlite3.connect(DB, timeout=20)) as db:
        db.row_factory = sqlite3.Row
        with db:
            yield db


def prepare():
    with database() as db:
        db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY, url TEXT UNIQUE, title TEXT, source TEXT,
            published INTEGER, body TEXT, feed_hash TEXT, coverage TEXT, summary TEXT DEFAULT '',
            seen INTEGER DEFAULT 0, problem TEXT DEFAULT '');
          CREATE TABLE IF NOT EXISTS web_sources (id TEXT PRIMARY KEY,url TEXT NOT NULL,name TEXT NOT NULL,
            pattern TEXT NOT NULL,error TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS web_links (source TEXT,key TEXT,url TEXT,title TEXT,day TEXT,pending INTEGER,
            PRIMARY KEY(source,key));
          CREATE TABLE IF NOT EXISTS history (feed TEXT, item TEXT, PRIMARY KEY(feed,item));
          CREATE TABLE IF NOT EXISTS article_feeds (article TEXT, feed TEXT, PRIMARY KEY(article,feed));
          CREATE TABLE IF NOT EXISTS initialized_feeds (feed TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM preferences WHERE key='schema_initialized'").fetchone():
            db.execute('INSERT OR IGNORE INTO initialized_feeds SELECT DISTINCT feed FROM history')
            db.execute("INSERT INTO preferences VALUES('schema_initialized','1')")
        if 'discovered' not in {r[1] for r in db.execute('PRAGMA table_info(articles)')}:
            db.execute("ALTER TABLE articles ADD COLUMN discovered TEXT NOT NULL DEFAULT ''")


def canonical(url):
    p = parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('文章链接无效')
    scheme = 'https' if p.hostname in SOURCES else p.scheme
    query = [(k, v) for k, v in parse.parse_qsl(p.query, keep_blank_values=True)
             if not k.startswith('utm_') and k not in ('fbclid', 'gclid')]
    return parse.urlunsplit((scheme, p.netloc.lower(), p.path.rstrip('/') or '/', parse.urlencode(query), ''))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Text(HTMLParser):
    """Extract plain text, optionally restricted to the blog's article body."""
    void = {'img','br','hr','input','link','meta','source','area','base','embed','param','track','wbr','col'}
    blocks = {'p','div','h1','h2','h3','h4','li','blockquote','section','br','pre','tr'}

    def __init__(self, article_only=False, paragraphs_only=False):
        super().__init__()
        self.paragraphs_only = paragraphs_only
        self.article_only = article_only
        self.stack = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        active, hidden = self.stack[-1][1:] if self.stack else (not self.article_only, False)
        active = active or 'post-content' in (dict(attrs).get('class') or '').split() or tag == self.article_only
        hidden = hidden or tag in ('style','script','nav','svg','noscript')
        if tag in self.blocks and active and not hidden:
            self.parts.append('\n')
        if tag not in self.void:
            self.stack.append((tag, active, hidden))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack:
            _, active, hidden = self.stack[-1]
        else:
            active, hidden = not self.article_only, False
        if self.paragraphs_only and not any(tag=='p' for tag,_,_ in self.stack):
            return
        if active and not hidden:
            self.parts.append(re.sub(r'\s+', ' ', data))

    def extract(self, html):
        self.feed(html)
        return '\n'.join(s for line in ''.join(self.parts).splitlines() if (s := ' '.join(line.split())))


def article_text(url, html):
    body = Text().extract(html)
    host = parse.urlsplit(url).hostname
    if host == 'lilianweng.github.io' or len(body) < 500:
        try:
            with request.urlopen(request.Request(url, headers={'User-Agent':'ResearchInbox/1.0'}), timeout=16) as response:
                markup = response.read(4_000_000).decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                full = Text('article' if host != 'lilianweng.github.io' else True).extract(markup)
                if not full and host != 'lilianweng.github.io':
                    full = Text('main').extract(markup)
            if len(full) > len(body):
                return full, '原文'
        except (OSError, ValueError):
            pass
        return body, '订阅导语'
    return body, '订阅正文' if host == 'bair.berkeley.edu' else '订阅内容'


def ingest(sub, get):
    feed = sub['id']
    with database() as db:
        initialized = db.execute('SELECT 1 FROM initialized_feeds WHERE feed=?',(feed,)).fetchone() is not None
        known = {r[0] for r in db.execute('SELECT item FROM history WHERE feed=?', (feed,))}
    entries = []
    continuation = ''
    while True:
        page = get('stream/contents/' + feed + '?n=100' + continuation)
        batch = page.get('items', [])
        entries.extend(batch)
        if not page.get('continuation') or (known and any(i['id'] in known for i in batch)):
            break
        continuation = '&c=' + parse.quote(page['continuation'], safe='')
    first = {i['id'] for i in sorted(entries, key=lambda i: i['published'], reverse=True)[:3]}
    for item in entries:
        links = item.get('alternate') or item.get('canonical') or []
        if not links:
            continue
        url = canonical(links[0]['href'])
        aid = digest(url)[:20]
        with database() as db:
            old = db.execute('SELECT feed_hash,coverage FROM articles WHERE id=?', (aid,)).fetchone()
        if not old and (item['id'] in known or (not initialized and item['id'] not in first)):
            continue
        html = (item.get('summary') or item.get('content') or {}).get('content','')
        fingerprint = digest(html)
        if old and old['feed_hash'] == fingerprint and old['coverage'] != '订阅导语':
            with database() as db:
                db.execute('INSERT OR IGNORE INTO article_feeds VALUES(?,?)',(aid,feed))
                db.execute('UPDATE articles SET title=?,published=? WHERE id=?',
                           (unescape(item['title']),item['published'],aid))
            continue
        body, coverage = article_text(url, html)
        source = SOURCES.get(parse.urlsplit(url).hostname, sub['title'])
        with database() as db:
            db.execute('''INSERT INTO articles(id,url,title,source,published,body,feed_hash,coverage,discovered)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,
              summary=CASE WHEN articles.body=excluded.body THEN articles.summary ELSE '' END,
              body=excluded.body,coverage=excluded.coverage,feed_hash=excluded.feed_hash''',
                       (aid,url,unescape(item['title']),source,item['published'],body,fingerprint,coverage,
                        datetime.now().date().isoformat() if initialized else ''))
            db.execute('INSERT OR IGNORE INTO article_feeds VALUES(?,?)',(aid,feed))
    with database() as db:
        db.execute('INSERT OR IGNORE INTO initialized_feeds VALUES(?)',(feed,))
        db.executemany('INSERT OR IGNORE INTO history VALUES(?,?)', [(feed,i['id']) for i in entries])


def evidence(body):
    """Select contiguous source sentences for the local model and the reader."""
    body = re.split(r'\n(?:References|BibTex|Citation)\s*\n', body, 1, flags=re.I)[0]
    candidates = []
    for line in body.splitlines():
        if re.match(r'(Figure \d|Table \d|Special thanks|Acknowledgment)', line, re.I):
            continue
        for sentence in re.split(r'(?<!\b[A-Z]\.)(?<=[.!?。])\s+(?=[A-Z“])', line):
            if 55 <= len(sentence) <= 700:
                candidates.append(sentence)
    if not candidates:
        return [body[:1600]] if body.strip() else []
    indicative = re.compile(r'\b(this (?:post|work|article|paper)|we (?:address|ask|built|introduce|propose|find|show|present|demonstrate)|our (?:approach|results|method)|results show)\b', re.I)
    selected = {0, min(1,len(candidates)-1)}
    for i, sentence in enumerate(candidates):
        if indicative.search(sentence) and 'not be the focus' not in sentence:
            selected.add(i)
        if len(selected) >= 6:
            break
    if len(selected) < 5:
        selected.update(range(min(7,len(candidates))))
    return [candidates[i] for i in sorted(selected)]


def summarize(row, complete=None):
    passages = evidence(row['body'])
    if not passages:
        raise ValueError('没有可供摘要的正文')
    for group in (passages, passages[:2]):
        facts = '\n'.join(group)
        messages = [{'role':'system','content':'你是严谨的中译编辑。只转述提供的原句，保留方法名。'},
                    {'role':'user','content':f'<source>\n{facts}\n</source>\n为刚看到这篇文章的读者写两句简体中文导读，总长约100字。用第三人称概括主题和最值得阅读的一点。略去无关背景和具体倍数。直接回答。'}]
        payload = {'messages':messages,'temperature':0,'max_tokens':400,'stream':False}
        if complete:
            result = complete(payload)
        else:
            result = json.loads(local(MODEL_URL + '/v1/chat/completions', json.dumps(payload).encode(),
                                      {'Content-Type':'application/json'}, timeout=180))
        summary = result['choices'][0]['message']['content'].strip()
        numbers = set(re.findall(r'\d+(?:\.\d+)?',summary))
        source_numbers = set(re.findall(r'\d+(?:\.\d+)?',facts))
        for month in range(1,13):
            if calendar.month_name[month] in facts:
                source_numbers.add(str(month))
        if len(re.findall(r'[\u4e00-\u9fff]', summary)) >= 20 and numbers <= source_numbers:
            return summary
    raise ValueError('导读语言或数字与原文不符，等待重试')



def sync(notify, get, prepare_model, refresh_web=None):
    errors = []
    notify('正在刷新订阅')
    if get is not None:
        try:
            php('actualize-user', '--user', 'reader')
            subscriptions = get('subscription/list?output=json')['subscriptions']
        except Exception as exc:
            subscriptions = []
            errors.append('RSS：'+str(exc))
        for sub in subscriptions:
            try:
                ingest(sub, get)
            except Exception as e:
                errors.append(f"{sub['title']}：{e}")
    if refresh_web:
        errors.extend(refresh_web(notify))
    with database() as db:
        pending = list(db.execute("SELECT * FROM articles WHERE summary='' ORDER BY published DESC"))
    complete = prepare_model() if pending else None
    for index, row in enumerate(pending, 1):
        notify(f'生成中文导读 {index}/{len(pending)}')
        try:
            summary = summarize(row, complete)
            problem = ''
        except Exception as e:
            summary, problem = '', str(e)
            errors.append(row['title'] + '：' + problem)
        with database() as db:
            db.execute('UPDATE articles SET summary=?,problem=? WHERE id=?',(summary,problem,row['id']))
    return '；'.join(errors)

