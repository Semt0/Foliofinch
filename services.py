"""Local FreshRSS and llama.cpp processes for the Mac edition."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib import request, parse

ROOT = Path(__file__).resolve().parent
PACKAGED = (ROOT / 'python/bin/python3.13').is_file()
default_data = Path.home() / 'Library/Application Support/Foliofinch' if PACKAGED else ROOT / '.runtime'
DATA = Path(os.environ.get('FOLIOFINCH_DATA', default_data))
if PACKAGED:
    os.environ.setdefault('SSL_CERT_FILE', '/etc/ssl/cert.pem')
FRESH = DATA / 'FreshRSS-1.30.4'
RSS_URL = 'http://127.0.0.1:8732'
MODEL_URL = 'http://127.0.0.1:8733'
local_http = request.build_opener(request.ProxyHandler({}))


def local(url, data=None, headers=None, timeout=20):
    req = request.Request(url, data=data, headers=headers or {})
    with local_http.open(req, timeout=timeout) as response:
        return response.read()


def php(command, *args):
    result = subprocess.run([str(DATA / 'frankenphp'), 'php-cli', str(FRESH / 'cli' / (command + '.php')), *args],
                            cwd=FRESH, timeout=180, capture_output=True, text=True)
    # FreshRSS exits 1 for a healthy refresh when every feed is inside its TTL.
    if command == 'actualize-user' and result.returncode == 1 and 'FreshRSS actualized 0 feeds' in result.stdout:
        return
    if result.returncode:
        with (DATA / 'fresh-cli.log').open('a') as log:
            log.write(result.stderr)
        raise RuntimeError(f'FreshRSS {command} 未完成（退出码 {result.returncode}），详见 .runtime/fresh-cli.log')


def prepare_components(group):
    """Called under the refresh lock, only when this component is needed."""
    if group == 'rss':
        ready = os.access(DATA / 'frankenphp', os.X_OK) and (FRESH / 'cli/do-install.php').is_file()
    else:
        ready = os.access(DATA / 'llama/llama-server', os.X_OK) and (DATA / 'qwen.gguf').is_file()
    if ready:
        return
    import inbox
    phase = '首次使用，正在下载订阅组件…' if group == 'rss' else '正在下载本地模型（约 1.12 GB）与推理组件…'
    with inbox.database() as db:
        db.execute('INSERT OR REPLACE INTO preferences VALUES(?,?)', ('phase', phase))
    log = DATA / 'setup.log'
    with log.open('ab') as stream:
        result = subprocess.run(['/bin/bash', str(ROOT / 'setup.command'), group],
                                env=dict(os.environ, FOLIOFINCH_DATA=str(DATA)),
                                stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'组件安装失败，请检查网络后点「立即检查」重试。详情见 {log}')


def credentials():
    file = DATA / 'freshrss-login.json'
    if not file.exists():
        with open(file, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            json.dump({'user': 'reader', 'password': secrets.token_urlsafe(24),
                       'api_password': secrets.token_urlsafe(24)}, stream)
    return json.loads(file.read_text())


def initialize():
    auth = credentials()
    if not (FRESH / 'data/config.php').exists():
        php('prepare')
        php('do-install', '--default-user', 'reader', '--auth-type', 'form', '--base-url',
            RSS_URL, '--api-enabled', '--db-type', 'sqlite', '--language', 'zh-CN')
    if not (FRESH / 'data/users/reader/config.php').exists():
        php('create-user', '--user', 'reader', '--password', auth['password'],
            '--api-password', auth['api_password'], '--no-default-feeds', '--language', 'zh-CN')
    marker = DATA / 'subscriptions-initialized'
    if not marker.exists():
        php('import-for-user', '--user', 'reader', '--filename', str(ROOT / 'feeds.opml'))
        marker.touch()


def start_freshrss(children):
    prepare_components('rss')
    initialize()
    config = DATA / 'Caddyfile'
    config.write_text('''{
    admin off
    auto_https off
    persist_config off
    frankenphp
}
http://127.0.0.1:8732 {
    bind 127.0.0.1
    root * "./p"
    php_server
}
''')
    launch(children, [str(DATA / 'frankenphp'), 'run', '--config', str(config)],
           FRESH, RSS_URL + '/api/greader.php/check/compatibility', 'freshrss')


def start_model(children):
    prepare_components('model')
    server = next((DATA / 'llama').rglob('llama-server'))
    launch(children, [str(server), '-m', str(DATA / 'qwen.gguf'), '--host', '127.0.0.1',
                      '--port', '8733', '--cors-origins', 'localhost', '-c', '8192', '-np', '1'],
           server.parent, MODEL_URL + '/health', 'model')


def launch(children, args, cwd, health, name):
    env = dict(os.environ, XDG_DATA_HOME=str(DATA / 'xdg'), XDG_CONFIG_HOME=str(DATA / 'xdg'))
    with (DATA / (name + '.log')).open('ab') as log:
        child = subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=log)
    children.append(child)
    for _ in range(150):
        if child.poll() is not None:
            raise RuntimeError(f'{name} 启动失败，请看 .runtime/{name}.log')
        try:
            local(health, timeout=1)
            return
        except OSError:
            time.sleep(.3)
    raise RuntimeError(f'{name} 启动超时，请看 .runtime/{name}.log')


def api_client():
    auth = credentials()
    base = RSS_URL + '/api/greader.php'
    data = parse.urlencode({'Email': auth['user'], 'Passwd': auth['api_password']}).encode()
    login = local(base + '/accounts/ClientLogin', data).decode()
    token = next(line[5:] for line in login.splitlines() if line.startswith('Auth='))
    def call(path, fields=None, raw=False):
        headers = {'Authorization': 'GoogleLogin auth=' + token}
        payload = None
        if fields is not None:
            action_token = call('token', raw=True).strip()
            payload = parse.urlencode(dict(fields, T=action_token)).encode()
        result = local(base + '/reader/api/0/' + path, payload, headers, timeout=90)
        return result.decode() if raw else json.loads(result)
    return call
