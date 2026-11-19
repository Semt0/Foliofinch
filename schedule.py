"""Install or uninstall the local user's daily refresh job."""
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from urllib.parse import urlsplit

import inbox
from app import JOB, write_private, prefs
from services import ROOT, DATA

target=Path.home()/'Library/LaunchAgents'/(JOB+'.plist')
domain='gui/'+str(os.getuid())
job=domain+'/'+JOB


def installed():
    return subprocess.run(['launchctl','print',job],capture_output=True).returncode==0


DEFAULT = dict(frequency='daily', hour=9, minute=0, weekday=1)


def settings(preferences):
    return json.loads(preferences['schedule']) if 'schedule' in preferences else DEFAULT.copy()


def validate(plan):
    if not isinstance(plan, dict) or plan.get('frequency') not in ('hourly', 'daily', 'weekly'):
        raise ValueError('请选择有效的更新频率')
    for key, low, high in [('hour', 0, 23), ('minute', 0, 59), ('weekday', 1, 7)]:
        if type(plan.get(key)) is not int or not low <= plan[key] <= high:
            raise ValueError('更新时间无效')
    return {key: plan[key] for key in DEFAULT}


def calendar(plan):
    interval = {'Minute': plan['minute']}
    if plan['frequency'] != 'hourly':
        interval['Hour'] = plan['hour']
    if plan['frequency'] == 'weekly':
        interval['Weekday'] = plan['weekday'] % 7  # launchd uses Sunday = 0
    return interval


def latest_slot(now, plan):
    slot = now.replace(minute=plan['minute'], second=0, microsecond=0)
    if plan['frequency'] == 'hourly':
        period = timedelta(hours=1)
    else:
        slot = slot.replace(hour=plan['hour'])
        period = timedelta(days=1)
        if plan['frequency'] == 'weekly':
            slot -= timedelta(days=(slot.isoweekday() - plan['weekday']) % 7)
            period = timedelta(weeks=1)
    return slot if slot <= now else slot - period


def due(now, preferences):
    slot = latest_slot(now, settings(preferences))
    changed = preferences.get('schedule_since')
    if changed and slot < datetime.fromisoformat(changed):
        return False
    if preferences.get('error'):
        return True
    success = preferences.get('last_success')
    if not success and preferences.get('day'):
        success = preferences['day'] + 'T09:00:00'
    return not success or datetime.fromisoformat(success) < slot


def install(plan=None):
    DATA.mkdir(parents=True, exist_ok=True)
    inbox.prepare()
    with (DATA/'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = settings(prefs())
        wanted = old if plan is None else validate(plan)
        if wanted == old:
            register(wanted)
            return
        try:
            register(wanted)
            with inbox.database() as db:
                db.executemany('INSERT OR REPLACE INTO preferences VALUES(?,?)',
                               [('schedule', json.dumps(wanted)), ('schedule_since', datetime.now().isoformat())])
        except Exception:
            register(old)
            raise


def register(plan):
    proxy={}
    for key in ('https_proxy','http_proxy','all_proxy'):
        value=os.environ.get(key) or os.environ.get(key.upper(),'')
        parsed=urlsplit(value)
        if parsed.hostname in ('127.0.0.1','localhost','::1') and not parsed.username and not parsed.password:
            proxy[key]=value
    if proxy:
        write_private(DATA/'network.json',proxy)
    definition=dict(Label=JOB,ProgramArguments=[sys.executable,'-B','-E','-s',str(ROOT/'app.py'),'daily'],
        WorkingDirectory=str(ROOT),RunAtLoad=True,StartCalendarInterval=calendar(plan),
        StartInterval=3600, StandardOutPath=str(DATA/'daily.log'),StandardErrorPath=str(DATA/'daily-error.log'))
    target.parent.mkdir(parents=True,exist_ok=True)
    content=plistlib.dumps(definition)
    if target.exists() and target.read_bytes()==content and installed():
        return
    if installed():
        subprocess.run(['launchctl','bootout',job],check=True)
    tmp=target.with_suffix('.tmp')
    tmp.write_bytes(content)
    tmp.replace(target)
    subprocess.run(['launchctl','enable',job],check=True)
    subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True)


if __name__=='__main__':
    if sys.argv[1:] == ['remove']:
        if installed():
            subprocess.run(['launchctl','bootout',job],check=True)
        target.unlink(missing_ok=True)
        print('每日更新已停用')
    elif sys.argv[1:] == ['install']:
        install()
        print('每日更新已启用')
    else:
        sys.exit('用法：python3 schedule.py install|remove')
