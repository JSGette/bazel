from pathlib import Path
import json,re,hashlib
R=Path('/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25')
p=json.loads((R/'results.json').read_text())
assert p['status']=='failed' and p['failure']=="AssertionError('prepare-skycache')"
diagnostics={
 'reason':'Vanilla setup finished successfully but six remote-cache ByteStreamUploader RPCs exceeded the 120-second deadline. Both measured variants use a 600-second timeout and reject remote-cache warnings.',
 'preparation_is_not_comparative_timing':True,
 'skycache_first_publication':'Discarded because inputs changed during invocation. Fresh external-repository materialization is a hypothesis; no exact changed witness was emitted. Repeat with the existing outputs, freshly started servers and frozen MODULE lockfile. Keep the original attempt.',
 'records':[]}
for r in p['runs']:
 text=(R/(r['label']+'.log')).read_text()
 diagnostics['records'].append({
  'label':r['label'],'exit_code':r['exit_code'],'seconds':r['seconds'],
  'warnings':re.findall(r'^WARNING: Remote Cache: (.*)',text,re.M),
  'deadline_exception_count':text.count('Caused by: io.grpc.StatusRuntimeException: DEADLINE_EXCEEDED'),
  'upload_stack_present':'com.google.devtools.build.lib.remote.ByteStreamUploader$Writer.onError' in text,
  'runner_counts':r['runner_counts'],'output_sha256':r.get('output_sha256'),
  'publication_failures':r.get('publication_failures')})
(R/'cache-preparation-diagnosis.json').write_text(json.dumps(diagnostics,indent=2)+'\n')
rc=Path.home()/'.bazelrc'
text=rc.read_text();assert '--remote_timeout=120' in text
rc.write_text(text.replace('--remote_timeout=120','--remote_timeout=600'))
print(json.dumps(diagnostics,indent=2))
