"""Preserve the old/new Python-symlink witness discrepancy without changing it."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,importlib.util,json,os,stat,struct
P=Path(__file__).with_name('inspect-skycache-manifests.py')
spec=importlib.util.spec_from_file_location('manifest_inspector',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
assert json.loads((m.ROOT/'results.json').read_text())['status']=='passed'
comparison=json.loads((m.ROOT/'manifest-comparison-before-r2.json').read_text())
differences=comparison['differences'][0]['differences']
assert len(differences)==900 and all('__pycache__' in d['path'] for d in differences)
d=differences[0];path=m.resolve(d['path']);target=os.readlink(path)
assert path.is_symlink() and not path.exists()
actual=m.fingerprint(path,False);assert actual==d['after']
historical_modes=[]
for mode in range(512):
 h=hashlib.sha256();m.update(h,'symlink');m.update(h,stat.filemode(path.lstat().st_mode)[1:]);m.update(h,target)
 m.update(h,'directory');m.update(h,stat.filemode(stat.S_IFDIR|mode)[1:])
 if h.hexdigest()==d['before']:historical_modes.append(oct(mode))
assert historical_modes
result={'observed_at':datetime.now(timezone.utc).isoformat(),'difference_count':len(differences),
 'all_differences_in_python_pycache':True,'old_publication':comparison['publications'][0]['id'],
 'replacement_publication':comparison['publications'][1]['id'],
 'same_namespace':comparison['publications'][0]['namespace']==comparison['publications'][1]['namespace'],
 'same_source_digest':comparison['publications'][0]['source']==comparison['publications'][1]['source'],
 'example':dict(d,resolved_path=str(path),symlink_target=target,current_target_exists=path.exists(),actual_digest=actual,old_digest_matches_directory_referent_modes=historical_modes),
 'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'uptime_seconds':float(Path('/proc/uptime').read_text().split()[0]),
 'conclusion':'The old snapshot requires Python __pycache__ symlinks with existing directory referents. Current referents are missing, invalidating its global witnesses. The replacement snapshot describes dangling symlinks and successfully restores on subsequent clean builds. The deletion event itself was not captured; do not attribute it to bazel clean.'}
(m.ROOT/'snapshot-diagnosis.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
