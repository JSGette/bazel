from pathlib import Path
import urllib.request, tarfile, os, sys
from importlib.machinery import SourceFileLoader
p=SourceFileLoader('probe',str(Path(__file__).with_name('windows-probe.py'))).load_module()
name='msys2-base-x86_64-20260927.tar.xz'
url='https://github.com/msys2/msys2-installer/releases/download/2026-09-27/'+name
archive=p.ROOT/name
urllib.request.urlretrieve(url,archive)
checksum=urllib.request.urlopen(url+'.sha256').read().decode()
assert p.sha(archive)==checksum.split()[0], checksum
p.save(p.LOG/'msys-provenance.json',{'url':url,'sha256':p.sha(archive),'upstream_sha256':checksum})
with tarfile.open(archive) as t:
    t.extractall(p.ROOT, filter='data')
env=dict(os.environ, MSYSTEM='MSYS', CHERE_INVOKING='1')
r=p.run('msys-install-zip', ['E:/scw/msys64/usr/bin/bash.exe','-lc','pacman -Sy --noconfirm --needed zip unzip'], env=env)
sys.exit(r['exit_code'])
