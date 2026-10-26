from pathlib import Path
import gzip,hashlib,json,re,subprocess
repo=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
dest=repo/'docs/jsgette/measurements/skycache-digests-cold-2026-10-26'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
selected=json.loads((dest/'build-slots-v2/result.json').read_text())
assert selected['cases']==635 and len(selected['tests'])==23
assert all(t['status']=='PASSED' and t['failures']==t['errors']==0 for t in selected['tests'])
assert all(sha(repo/p)==digest for p,digest in selected['source_sha256'].items())
assert json.loads((dest/'registry-comparison.json').read_text())['identical']
measured=diagnostic=0
for cohort in ['warm','cold','eager','empty','diagnostic','confirm','slots-warm','slots-cold']:
 c=json.loads((dest/(cohort+'-comparison.json')).read_text())
 assert c['agent_verified'] and c['witness_tables_identical']
 assert all(g['n']==(1 if cohort=='diagnostic' else 3) for g in c['summary'].values())
 if cohort=='diagnostic':diagnostic+=len(c['rows'])
 else:measured+=len(c['rows'])
 for row in c['rows']:
  if 'page_cache' in row:assert row['page_cache']['resident_fraction_after']<.001
for name in ['verification.json','evidence-index.json']:
 if not (dest/name).exists():(dest/name).write_text('{}\n')
checked=links=0
for path in dest.rglob('*'):
 if not path.is_file():continue
 if path.name.endswith('.json.gz'):json.loads(gzip.decompress(path.read_bytes()))
 elif path.name.endswith('.jsonl.gz'):
  for line in gzip.decompress(path.read_bytes()).splitlines():json.loads(line)
 elif path.suffix=='.json':json.loads(path.read_text())
 elif path.suffix=='.py':compile(path.read_text(),str(path),'exec')
 checked+=1
for md in [dest/'README.md',repo/'docs/jsgette/skycache-remote-mvp.md']:
 for target in re.findall(r'\]\(([^)]+)\)',md.read_text()):
  if '://' in target or target.startswith('#'):continue
  name,_,anchor=target.partition('#');path=(md.parent/name).resolve()
  assert path.exists(),(md,target)
  if re.fullmatch(r'L\d+',anchor):assert int(anchor[1:])<=len(path.read_text().splitlines())
  links+=1
subprocess.run(['git','diff','--check'],cwd=repo,check=True)
verification=dict(source_matches_tested_build=True,cases=635,targets=23,measured_commands=measured,
 diagnostic_commands=diagnostic,registry_identical=True,files_checked=checked,local_links_checked=links,
 selected_binary_sha256=selected['binary_sha256'],all_cohort_invariants_passed=True)
(dest/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
index=[dict(path=str(p.relative_to(dest)),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(dest.rglob('*'))
 if p.is_file() and p.name not in ['README.md','evidence-index.json']]
(dest/'evidence-index.json').write_text(json.dumps(index,indent=2)+'\n')
print(json.dumps(verification,indent=2))
