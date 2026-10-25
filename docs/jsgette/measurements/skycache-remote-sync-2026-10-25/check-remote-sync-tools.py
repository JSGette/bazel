from pathlib import Path
import json, hashlib, stat, os, shutil
R = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
roots = [Path('/home/jgett/.cache/bazel/_bazel_jgett/install/80a15f52b9b3373384f22804a4dc15c5/embedded_tools'),
         (R / 'remote/out-writer/external/bazel_tools').resolve(strict=True)]
def inventory(root):
    result = {}
    for p in sorted(root.rglob('*')):
        s = p.lstat()
        if stat.S_ISDIR(s.st_mode): continue
        v = {'mode': stat.S_IMODE(s.st_mode)}
        v['link' if p.is_symlink() else 'sha256'] = os.readlink(p) if p.is_symlink() else hashlib.sha256(p.read_bytes()).hexdigest()
        result[str(p.relative_to(root))] = v
    return result
a, b = map(inventory, roots)
result = dict(roots=list(map(str, roots)), counts=[len(a), len(b)],
              differences=[dict(path=k, baseline=a.get(k), merged=b.get(k)) for k in sorted(a.keys() | b.keys()) if a.get(k) != b.get(k)],
              benchmark_started=False, lockfile_modified=False)
(R / 'embedded-tools-preflight.json').write_text(json.dumps(result, indent=2) + '\n')
shutil.copy2('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work/benchmark-skycache-remote-sync.py', R / 'benchmark-initial-preflight.py')
print(json.dumps(result, indent=2))
