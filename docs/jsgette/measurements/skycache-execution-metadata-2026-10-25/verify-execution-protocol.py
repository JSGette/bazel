from pathlib import Path
import hashlib,json,shutil,subprocess,xml.etree.ElementTree as ET
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
D=Path('/home/jgett/REPOS/bazel-skyframe-findings')
S=Path('/home/jgett/REPOS/bazel-skycache-v2')
E=D/'docs/jsgette/measurements/skycache-execution-metadata-2026-10-25'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
counts=[]
for p in sorted((E/'test-xml').glob('*.xml')):
 cases=list(ET.parse(p).getroot().iter('testcase'))
 row={'target':p.stem,'tests':len(cases),**{k:sum(len(c.findall(tag)) for c in cases) for k,tag in [('failures','failure'),('errors','error'),('skipped','skipped')]}}
 assert row['tests']>0 and row['failures']==row['errors']==row['skipped']==0
 counts.append(row)
save(E/'test-summary.json',counts)
remote=next(x for x in counts if x['target']=='RemoteTests');assert remote['tests']==154
report=D/'docs/jsgette/skycache-execution-metadata-2026-10-25.md'
report.write_text(report.read_text().replace('The remote-filesystem suite passed 844 cases','The filtered remote-filesystem suite passed 154 executed cases'))
shutil.copy2(report,T/'outputs/SkyCache-execution-metadata.md')
data=json.loads((E/'execution-metadata/results.json').read_text())
for r in data['runs']:
 if r['preparation'] or r['variant']!='on':continue
 assert r['execution_hits']==6 and r['analysis_hits']==12
 assert r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)==0
 assert r['grpc']['method_attempt_counts']['build.bazel.remote.execution.v2.Capabilities/GetCapabilities']==1
 if r['mode']=='minimal':
  assert r['grpc']['bytestream_read_bytes']==r['grpc']['bytestream_read_calls']==0
  assert r['output_inventory']['regular_file_bytes']==45186
 else:
  assert r['grpc']['bytestream_read_calls']==5 and r['grpc']['bytestream_read_bytes']==121907152
data['implementation_commit']='71521fabb3420707c39d73329fa9aa9622e8c382'
data['source_patch_note']='Original source_patch_sha256 was a tracked-worktree diagnostic before commit. Complete source identity, including new files, is recorded by implementation_commit and provenance.json; patches are archived separately.'
save(E/'execution-metadata/results.json',data)
for name in ['implementation-incremental.patch','implementation-current.patch']:
 subprocess.run(['git','-C',str(S),'apply','--check','--reverse',str(E/name)],check=True)
shutil.copy2(T/'work/publish-execution-protocol.py',E/'publish-execution-protocol.py')
shutil.copy2(__file__,E/'verify-execution-protocol.py')
old=D/'docs/jsgette/skycache-v2-datadog-clean-2026-10-25.md'
text=old.read_text();marker='\n## Follow-up: execution metadata reuse\n'
assert marker not in text
old.write_text(text+marker+'\nThe subsequent [execution-metadata implementation and clean-build comparison](skycache-execution-metadata-2026-10-25.md) removes intermediate action-result lookups. Its minimal mode makes zero CAS reads, and controlled missing-blob tests verify rewind recovery. The analysis-only measurements above remain the historical baseline.\n')
files=sorted(q for q in E.rglob('*') if q.is_file() and q.name!='SHA256SUMS')
(E/'SHA256SUMS').write_text(''.join(sha(q)+'  '+str(q.relative_to(E))+'\n' for q in files))
for line in (E/'SHA256SUMS').read_text().splitlines():
 digest,name=line.split('  ',1);assert sha(E/name)==digest
print(json.dumps({'verified_public_files':len(files),'executed_tests':sum(x['tests'] for x in counts),'remote_filesystem_cases':remote['tests'],'metadata_hit_builds':6,'patches_reverse_apply':True},indent=2))
