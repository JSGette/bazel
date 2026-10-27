from pathlib import Path
import gzip,hashlib,json,re,subprocess
REPO=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
DEST=REPO/'docs/jsgette/measurements/skycache-retention-2026-10-27'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
build=json.loads((DEST/'build-advisory/result.json').read_text())
assert build['cases']==637 and len(build['tests'])==23 and build['exit_code']==0
assert all(t['status']=='PASSED' and t['failures']==t['errors']==0 for t in build['tests'])
assert all(sha(REPO/name)==digest for name,digest in build['source_sha256'].items())
assert json.loads((DEST/'registry-comparison.json').read_text())['identical']
agent=json.loads((DEST/'agent/provenance.json').read_text())
assert agent['baseline'].keys()==agent['retention'].keys()
assert all(v==agent['retention'][k] for k,v in agent['baseline'].items() if '/RemoteChunkCache' not in k)
primary=diagnostic=0;witnesses=[];residencies=[]
for cohort in ['cold','warm','diagnostic-before-v2','diagnostic-after']:
 c=json.loads((DEST/(cohort+'-comparison.json')).read_text())
 assert c['agent_verified'] and c['witness_tables_identical']
 assert all(g['n']==(1 if cohort.startswith('diagnostic') else 3) for g in c['summary'].values())
 if cohort.startswith('diagnostic'):diagnostic+=len(c['rows'])
 else:primary+=len(c['rows'])
 residencies += [r['page_cache']['resident_fraction_after'] for r in c['rows'] if 'page_cache' in r]
 assert all(v<.001 for v in residencies)
 assert all(r['remote']['reused_publications']==2 for r in c['rows'] if 'remote' in r)
 witnesses+=json.loads((DEST/(cohort+'-witnesses.json')).read_text())
assert witnesses and all(w['hashes']==witnesses[0]['hashes'] for w in witnesses)
for name in ['verification.json','evidence-index.json']:
 if not (DEST/name).exists():(DEST/name).write_text('{}\n')
checked=links=0
for path in DEST.rglob('*'):
 if not path.is_file():continue
 if path.name.endswith('.json.gz'):json.loads(gzip.decompress(path.read_bytes()))
 elif path.name.endswith('.jsonl.gz'):
  for line in gzip.decompress(path.read_bytes()).splitlines():json.loads(line)
 elif path.suffix=='.json':json.loads(path.read_text())
 elif path.suffix=='.py':compile(path.read_text(),str(path),'exec')
 checked+=1
for path in [DEST/'README.md',REPO/'docs/jsgette/skycache-remote-mvp.md']:
 for target in re.findall(r'\]\(([^)]+)\)',path.read_text()):
  if '://' in target or target.startswith('#'):continue
  name,_,anchor=target.partition('#');p=(path.parent/name).resolve();assert p.exists(),(path,target)
  if re.fullmatch(r'L\d+',anchor):assert int(anchor[1:])<=len(p.read_text().splitlines())
  links+=1
subprocess.run(['git','diff','--check'],cwd=REPO,check=True)
verification=dict(source_matches_tested_build=True,cases=637,targets=23,primary_commands=primary,
 diagnostic_commands=diagnostic,registry_identical=True,files_checked=checked,local_links_checked=links,
 max_cold_resident_fraction=max(residencies),
 selected_binary_sha256=build['binary_sha256'],all_cohort_invariants_passed=True)
(DEST/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
index=[dict(path=str(p.relative_to(DEST)),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(DEST.rglob('*'))
 if p.is_file() and p.name not in ['README.md','evidence-index.json']]
(DEST/'evidence-index.json').write_text(json.dumps(index,indent=2)+'\n')
print(json.dumps(verification,indent=2))
