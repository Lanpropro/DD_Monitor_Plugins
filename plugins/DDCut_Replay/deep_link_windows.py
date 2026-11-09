"""Opt-in current-user URL registration; never replace another app's scheme."""
from pathlib import Path
import subprocess
import sys


REGISTRY_PATH = r'Software\Classes\ddcut'


def command(endpoint, launch):
    script = Path(__file__).with_name('deep_link_relay.ps1').resolve()
    return subprocess.list2cmdline(['powershell.exe','-NoProfile','-WindowStyle','Hidden',
        '-ExecutionPolicy','Bypass','-File',str(script),'-Endpoint',str(Path(endpoint).resolve()),
        '-Launch',str(Path(launch).resolve()),'-Uri','%1']).replace('-Uri %1','-Uri "%1"')


def register(endpoint, launch, *, registry_path=REGISTRY_PATH):
    if sys.platform!='win32':
        raise RuntimeError('当前快捷链接注册仅支持Windows')
    import winreg
    identity = str(Path(endpoint).resolve())
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry_path) as key:
            try:
                owned = winreg.QueryValueEx(key,'DDCutEndpoint')[0]==identity
            except FileNotFoundError:
                owned = False
            if not owned:
                raise RuntimeError('ddcut协议已由其他安装占用，请先在原安装中停用')
    except FileNotFoundError:
        pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,registry_path) as key:
        winreg.SetValueEx(key,'',0,winreg.REG_SZ,'URL:DDCut room link')
        winreg.SetValueEx(key,'URL Protocol',0,winreg.REG_SZ,'')
        winreg.SetValueEx(key,'DDCutEndpoint',0,winreg.REG_SZ,identity)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,registry_path+r'\shell\open\command') as key:
        winreg.SetValueEx(key,'',0,winreg.REG_SZ,command(endpoint,launch))


def unregister(endpoint, *, registry_path=REGISTRY_PATH):
    if sys.platform!='win32':
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,registry_path) as key:
            if winreg.QueryValueEx(key,'DDCutEndpoint')[0]!=str(Path(endpoint).resolve()):
                raise RuntimeError('快捷链接属于其他安装，未移除')
    except FileNotFoundError:
        return
    for suffix in (r'\shell\open\command',r'\shell\open',r'\shell',''):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,registry_path+suffix)
        except FileNotFoundError:
            pass
