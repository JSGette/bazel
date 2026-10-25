from pathlib import Path
import subprocess, json, hashlib
S = Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
R = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
paths = ['src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalSkycacheModule.java',
         'src/main/java/com/google/devtools/build/lib/skyframe/serialization/SerializationModule.java',
         'src/main/java/com/google/devtools/build/lib/skyframe/SkyframeExecutor.java',
         'src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis/RemoteAnalysisCacheDeps.java']
result = dict(correction='The earlier report incorrectly attributed idle retention to Services.client/session remaining set. LocalSkycacheModule calls super.afterCommand(), which invokes Services.resetCommandState() and clears both references. This already existed in the profiled 1aac2aa baseline.',
              next_investigation='SkyframeExecutor retains reader and manager dependency providers until noteCommandStart. RemoteAnalysisCacheDeps.LazyResolver retains the resolved client and fingerprint service. Confirm the complete retaining path and retained sizes in a heap dump before choosing a release point; preserve upload/cancellation quiescence and retained-root invalidation.',
              measured_histograms_remain_valid=True, heap_root_trace_collected=False, source=[])
for revision in ['1aac2aa2ec6fe31af2e979558f06a3b6971c601b', '0c80d7fe6847a3e628314fda4e10e6a5429f3d20']:
    for path in paths:
        data = subprocess.check_output(['git','show',revision + ':' + path], cwd=S)
        lines = data.decode().splitlines()
        markers = ['super.afterCommand()', 'void resetCommandState()', 'void afterCommand()',
                   'void noteCommandStart()', 'void notifyCommandComplete(',
                   'remoteAnalysisCacheReaderDepsProvider =', 'class LazyResolver<T>',
                   'this.analysisCacheClient =']
        locations = [i for i,line in enumerate(lines) if any(marker in line for marker in markers)]
        result['source'].append(dict(revision=revision, path=path, sha256=hashlib.sha256(data).hexdigest(),
                                     excerpts=[dict(start_line=i+1, text='\n'.join(lines[i:i+14])) for i in locations]))
(R/'memory-lifetime-review.json').write_text(json.dumps(result,indent=2)+'\n')
print('Recorded the correction and executor-owned dependency follow-up against both source snapshots.')
