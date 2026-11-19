"""Command-line bridge used by the native reader and its daily macOS job."""
import contextlib
import fcntl
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib import parse, request

import inbox
import services
import websources
import papers
import avatars

DATA = services.DATA
CONFIG = DATA / 'summary-provider.json'
JOB = 'local.blogdaily.refresh'


def put(key, value):
    with inbox.database() as db:
        db.execute('INSERT OR REPLACE INTO preferences VALUES(?,?)', (key, value))


def prefs():
    with inbox.database() as db:
        return dict(db.execute('SELECT key,value FROM preferences'))


def read_config():
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {'mode':'local','url':'','model':'','key':''}


def write_private(path, obj):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        json.dump(obj,f)
    os.replace(f.name,path)


def update_provider(form):
    cfg = read_config()
    if form['mode'] not in ('api','local'):
        raise ValueError('未知的摘要模式')
    url = form['url'].strip()
    key = form.get('key','').strip()
    if url != cfg['url'] and not key:
        cfg['key'] = ''
    cfg.update(mode=form['mode'],url=url,model=form['model'].strip(),key=key or cfg['key'])
    if cfg['mode'] == 'api':
        p = parse.urlsplit(url)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment or p.query:
            raise ValueError('请输入 HTTPS API 地址，请勿在地址中填写密钥')
        if not cfg['model'] or not cfg['key']:
            raise ValueError('请填写模型名称和 API 密钥')
    write_private(CONFIG,cfg)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def provider(children):
    cfg = read_config()
    if cfg['mode'] == 'local':
        services.start_model(children)
        return None
    url = cfg['url'].rstrip('/')
    if not url.endswith('/chat/completions'):
        url += '/chat/completions'
    opener = request.build_opener(NoRedirect())
    def complete(payload):
        req = request.Request(url, data=json.dumps(dict(payload,model=cfg['model'])).encode(),
                              headers={'Content-Type':'application/json','Authorization':'Bearer '+cfg['key']})
        with opener.open(req,timeout=90) as res:
            return json.load(res)
    return complete


def feeds():
    path = services.FRESH / 'data/users/reader/db.sqlite'
    if not path.exists():
        return websources.sources()
    with contextlib.closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        return [dict(id='feed/'+str(i),name=n,url=u,website=w,failed=bool(e)) for i,n,u,w,e in
                db.execute('SELECT id,name,url,website,error FROM feed ORDER BY name COLLATE NOCASE')] + websources.sources()


class FeedLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if (tag == 'link' and 'alternate' in (attr.get('rel') or '').lower().split()
              and (attr.get('type') or '').split(';')[0].strip().lower() in
                 ('application/atom+xml','application/rss+xml') and attr.get('href')):
            self.links.append(attr['href'])


def inspect_site(value, pattern='', force_web=False):
    url = value.strip()
    if '://' not in url:
        url = 'https://' + url
    inbox.canonical(url)
    base, markup = websources.fetch(url)
    if not force_web:
        direct = re.search(r'<(?:rss|feed|rdf:RDF)(?:\s|>)',markup[:12000],re.I)
        if direct or markup.lstrip().startswith('{'):
            return {'kind':'rss','url':base,'name':'','items':[]}
        info = papers.listing(base, markup)
        if info is None:
            links = FeedLinks()
            links.feed(markup)
            if links.links:
                result = parse.urljoin(base,links.links[0])
                inbox.canonical(result)
                return {'kind':'rss','url':result,'name':'','items':[]}
    else:
        info = None
    if info is None:
        info = websources.listing(base,pattern,page=(base,markup))
    info['count'] = len(info['items'])
    info['items'] = info['items'][:6]
    return info


def locate_feed(value):
    url = value.strip()
    if '://' not in url:
        url = 'https://' + url
    inbox.canonical(url)
    req = request.Request(url, headers={'User-Agent':'Foliofinch/1.0','Accept-Encoding':'identity'})
    with request.urlopen(req,timeout=20) as response:
        base = response.geturl()
        head = response.read(160000).decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
    links = FeedLinks()
    links.feed(head)
    if links.links:
        base = parse.urljoin(base,links.links[0])
        inbox.canonical(base)
    return base


def add_subscription(value, api):
    url = locate_feed(value)
    if any(s['url']==url for s in feeds()):
        return '已经订阅该来源'
    result = api('subscription/quickadd',{'quickadd':url})
    if result.get('numResults') != 1:
        raise ValueError('未能添加订阅。请检查网址，或粘贴网站的 RSS / Atom 订阅地址。')
    return '已添加 '+str(result.get('streamName','订阅'))


def active():
    with (DATA/'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def folder_action(action, args):
    with inbox.database() as db:
        if action in ('folder-create', 'folder-rename'):
            name = args[-1].strip()
            if not name:
                raise ValueError('请填写文件夹名称')
            try:
                if action == 'folder-create':
                    db.execute('INSERT INTO folders(name) VALUES(?)', (name,))
                elif not db.execute('UPDATE folders SET name=? WHERE id=?', (name,args[0])).rowcount:
                    raise ValueError('找不到该文件夹')
            except sqlite3.IntegrityError:
                raise ValueError('已有同名文件夹') from None
        elif action == 'folder-delete':
            db.execute('DELETE FROM folders WHERE id=?', (args[0],))
        elif action == 'folder-move':
            feed, folder = args
            if feed not in {f['id'] for f in feeds()}:
                raise ValueError('找不到该订阅')
            if folder:
                if not db.execute('SELECT 1 FROM folders WHERE id=?', (folder,)).fetchone():
                    raise ValueError('找不到该文件夹')
                db.execute('INSERT OR REPLACE INTO feed_folders VALUES(?,?)', (feed, folder))
            else:
                db.execute('DELETE FROM feed_folders WHERE feed=?', (feed,))
        return '订阅分类已保存'


def snapshot():
    from schedule import settings
    progress = prefs()
    options = read_config()
    with inbox.database() as db:
        rows = [dict(r) for r in db.execute('''SELECT id,title,source,url,published,coverage,
             summary,problem,discovered,seen,starred,queued,
             CASE WHEN coverage=? THEN body END AS originalAbstract
             FROM articles ORDER BY published DESC''',(papers.COVERAGE,))]
        folders = [dict(r) for r in db.execute('SELECT id,name FROM folders ORDER BY name COLLATE NOCASE')]
        groups = dict(db.execute('SELECT feed,folder FROM feed_folders'))
        members = {}
        for aid, fid in db.execute('SELECT article,feed FROM article_feeds'):
            members.setdefault(aid,[]).append(fid)
    for r in rows:
        r['feeds'] = members.get(r['id'],[])
    sources = feeds()
    for source in sources:
        source['folder'] = groups.get(source['id'])
        icon = avatars.cached_file(source)
        source['avatarPath'] = str(icon) if icon.is_file() else None
    return dict(articles=rows,feeds=sources,progress=progress,folders=folders,schedule=settings(progress),busy=active(),
                needsSetup=services.PACKAGED and not CONFIG.is_file(),
                mode=options['mode'],url=options['url'],model=options['model'],hasKey=bool(options['key']))


def restore_network():
    file=DATA/'network.json'
    if not file.exists():
        return
    for k,v in json.loads(file.read_text()).items():
        p=parse.urlsplit(v)
        if k in ('https_proxy','http_proxy','all_proxy') and p.hostname in ('127.0.0.1','localhost','::1'):
            try:
                with socket.create_connection((p.hostname,p.port),timeout=.4):
                    os.environ.setdefault(k,v)
            except OSError:
                pass


def run_job(command, value='', pattern=''):
    children=[]
    with (DATA/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        now=datetime.now()
        previous=prefs()
        from schedule import due
        if command=='daily' and not due(now, previous):
            return '本周期无需更新'
        try:
            if command == 'translate-papers':
                issues = inbox.generate_summaries(lambda s:put('phase',s), lambda:provider(children), papers_only=True)
                if issues:
                    raise ValueError('；'.join(issues))
                return '论文摘要已翻译'
            put('phase','正在连接订阅源')
            restore_network()
            if command=='remove' and value.startswith('feed/web-'):
                websources.remove(value)
                with inbox.database() as db:
                    db.execute('DELETE FROM feed_folders WHERE feed=?',(value,))
                return '已取消订阅'
            message=''
            if command in ('add-web', 'add-papers'):
                message=websources.register(value, pattern, 'papers' if command == 'add-papers' else 'web')
            rss_problem=''
            try:
                services.start_freshrss(children)
                api=services.api_client()
            except Exception as exc:
                if command in ('add','remove'):
                    raise
                api=None
                rss_problem='RSS：'+str(exc)
            if command=='add':
                message=add_subscription(value,api)
            if command=='remove':
                if value not in {s['id'] for s in feeds()}:
                    raise ValueError('找不到该订阅')
                if api('subscription/edit',{'s':value,'ac':'unsubscribe'},raw=True).strip()!='OK':
                    raise ValueError('取消订阅失败')
                with inbox.database() as db:
                    db.execute('DELETE FROM feed_folders WHERE feed=?',(value,))
                return '已取消订阅'
            issues=inbox.sync(lambda s:put('phase',s),api,lambda:provider(children),websources.refresh)
            issues='；'.join(filter(None,(rss_problem,issues)))
            failures=[s['name'] for s in feeds() if s['failed'] and not s['id'].startswith('feed/web-')]
            if failures:
                issues+=' 抓取失败：'+'、'.join(failures)
            put('error',issues.strip())
            put('last',datetime.now().strftime('%Y-%m-%d %H:%M'))
            if not issues:
                put('last_success',now.isoformat())
            return message or ('部分文章需要重试' if issues else '检查完成')
        except Exception as exc:
            if command != 'translate-papers':
                put('error',str(exc))
            raise
        finally:
            for child in children:
                child.terminate()
            for child in children:
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            put('phase','')


def main():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    inbox.prepare()
    cmd=sys.argv[1]
    if cmd in ('folder-create','folder-rename','folder-delete','folder-move'):
        return folder_action(cmd, sys.argv[2:])
    if cmd == 'schedule':
        from schedule import install
        install(json.load(sys.stdin))
        return '更新计划已保存'
    if cmd=='enable-daily':
        from schedule import install
        install()
        return '每日更新已启用'
    if cmd=='snapshot':
        return snapshot()
    if cmd=='avatar':
        restore_network()
        feed = next((f for f in feeds() if f['id']==sys.argv[2]),None)
        return avatars.resolve(feed) if feed else None
    if cmd=='inspect':
        restore_network()
        return inspect_site(sys.argv[2],sys.argv[3] if len(sys.argv)>3 else '',len(sys.argv)>4 and sys.argv[4]=='web')
    if cmd=='settings':
        update_provider(json.load(sys.stdin))
        return '已保存摘要设置'
    if cmd == 'delete':
        inbox.delete_article(sys.argv[2])
        return '已删除文章'
    flags = {'read':('seen',1), 'unread':('seen',0), 'star':('starred',1),
             'unstar':('starred',0), 'queue':('queued',1), 'unqueue':('queued',0)}
    if cmd in flags:
        column, value = flags[cmd]
        with inbox.database() as db:
            db.execute(f'UPDATE articles SET {column}=? WHERE id=?', (value, sys.argv[2]))
        return '文章状态已保存'
    if cmd not in ('add','add-web','add-papers','remove','refresh','daily','translate-papers'):
        raise ValueError('未知操作')
    return run_job(cmd,*sys.argv[2:])


if __name__=='__main__':
    try:
        print(json.dumps(main(),ensure_ascii=False))
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
