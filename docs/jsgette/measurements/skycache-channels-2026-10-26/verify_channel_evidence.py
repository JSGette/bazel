from pathlib import Path
import argparse, gzip, hashlib, json, re, shutil, subprocess

p=argparse.ArgumentParser();p.add_argument('--selected',type=int,choices=[128,256],required=True);a=p.parse_args()
repo=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
root=Path('/home/jgett/REPOS/.work/skycache-channels-2026-10-26')
dest=repo/'docs/jsgette/measurements/skycache-channels-2026-10-26'
shutil.copy2(Path(__file__),dest/'verify_channel_evidence.py')
for name in ['verification.json','evidence-index.json']:
 if not (dest/name).exists():(dest/name).write_text('{}\n')
def sha(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
selected=json.loads((root/('build-'+str(a.selected)+'-v2')/'result.json').read_text())
assert selected['cases']==632 and len(selected['tests'])==23
assert all(t['status']=='PASSED' and t['failures']==t['errors']==0 for t in selected['tests'])
assert all(sha(repo/name)==digest for name,digest in selected['source_sha256'].items())
assert json.loads((dest/'registry-comparison.json').read_text())['identical']
for cohort in ['warm','cold']:
 c=json.loads((dest/(cohort+'-comparison.json')).read_text())
 assert c['agent_configuration_verified'] and c['witness_tables_identical']
 assert all(x['n']==3 for x in c['summary'].values())
 if cohort=='cold':assert all(row['remote']['downloaded_bytes']==1_463_742_717 for row in c['rows'])
checked=0
for path in dest.rglob('*'):
 if not path.is_file():continue
 if path.name.endswith('.json.gz'):json.loads(gzip.decompress(path.read_bytes()))
 elif path.name.endswith('.jsonl.gz'):
  for line in gzip.decompress(path.read_bytes()).splitlines():json.loads(line)
 elif path.suffix=='.json':json.loads(path.read_text())
 elif path.suffix=='.py':compile(path.read_text(),str(path),'exec')
 checked+=1
links=0
for md in [dest/'README.md',repo/'docs/jsgette/skycache-remote-mvp.md']:
 for target in re.findall(r'\]\(([^)]+)\)',md.read_text()):
  if '://' in target or target.startswith('#'):continue
  name,_,anchor=target.partition('#');path=(md.parent/name).resolve()
  assert path.exists(),(md,target)
  if re.fullmatch(r'L\d+',anchor):assert int(anchor[1:])<=len(path.read_text().splitlines())
  links+=1
subprocess.run(['git','diff','--check'],cwd=repo,check=True)
verification=dict(selected_capacity=a.selected,source_matches_tested_build=True,cases=632,targets=23,
 selected_binary_sha256=selected['binary_sha256'],files_checked=checked,local_links_checked=links,
 registry_identical=True,warm_and_cold_invariants_passed=True)
(dest/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
index=[dict(path=str(path.relative_to(dest)),bytes=path.stat().st_size,sha256=sha(path))
 for path in sorted(dest.rglob('*')) if path.is_file() and path.name not in ['README.md','evidence-index.json']]
(dest/'evidence-index.json').write_text(json.dumps(index,indent=2)+'\n')
print(json.dumps(verification,indent=2))
