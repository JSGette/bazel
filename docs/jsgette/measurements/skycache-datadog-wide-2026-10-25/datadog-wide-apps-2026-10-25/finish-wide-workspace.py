"""Restore the exact pre-experiment lockfile after all measurements finish."""
from pathlib import Path
import hashlib,json,shutil,subprocess
W=Path('/home/jgett/REPOS/.work');R=W/'datadog-wide-apps-2026-10-25'
ws=Path('/home/jgett/REPOS/datadog-agent');lock=ws/'MODULE.bazel.lock'
p=json.loads((R/'results.json').read_text());assert p['status']=='passed' and p['workspace_unchanged']
def sha(q):return hashlib.sha256(q.read_bytes()).hexdigest()
assert sha(lock)==p['module_lock_sha256'],'Refuse to overwrite an intervening lockfile edit'
before=W/'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.before'
shutil.copy2(before,lock)
diff=subprocess.check_output(['git','-C',str(ws),'diff','--binary','HEAD']).decode().strip()+'\n'
assert diff==(W/'datadog-wide-2026-10-25/workspace.patch').read_text()
assert not subprocess.check_output(['git','-C','/home/jgett/REPOS/bazel-skycache-v2','status','--porcelain']).strip()
p['original_workspace_restored']=True;p['restored_lockfile_sha256']=sha(lock)
(R/'results.json').write_text(json.dumps(p,indent=2)+'\n')
(R/'protocol-notes.txt').write_text('The six- and five-binary scopes built correct outputs but failed SkyCache publication. No product code or limit changes were made. The six-application subset was selected for repeated comparison after inspecting the unpublished pack. Original MODULE.bazel.lock restored byte-for-byte after the measured matrix. A report-generator indentation error was caught by py_compile and corrected before publishing; it did not affect any build or measurement. Architecture Page: https://chatgpt.com/space/page_548d4eafded48191910332d966940603\n')
shutil.copy2(__file__,R/'finish-wide-workspace.py')
print(json.dumps({'original_workspace_restored':True,'lockfile_sha256':sha(lock)}))
