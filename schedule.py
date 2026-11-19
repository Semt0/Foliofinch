"""Install or uninstall the local user's daily refresh job."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from urllib.parse import urlsplit

from app import JOB, write_private
from services import ROOT, DATA

target=Path.home()/'Library/LaunchAgents'/(JOB+'.plist')
domain='gui/'+str(os.getuid())
job=domain+'/'+JOB


def installed():
    return subprocess.run(['launchctl','print',job],capture_output=True).returncode==0


def install():
    DATA.mkdir(exist_ok=True)
    proxy={}
    for key in ('https_proxy','http_proxy','all_proxy'):
        value=os.environ.get(key) or os.environ.get(key.upper(),'')
        parsed=urlsplit(value)
        if parsed.hostname in ('127.0.0.1','localhost','::1') and not parsed.username and not parsed.password:
            proxy[key]=value
    if proxy:
        write_private(DATA/'network.json',proxy)
    definition=dict(Label=JOB,ProgramArguments=['/usr/bin/python3',str(ROOT/'app.py'),'daily'],
        WorkingDirectory=str(ROOT),RunAtLoad=True,StartCalendarInterval={'Hour':9,'Minute':0},
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
