"""Shared, explicit controls for the full-repository SkyCache comparison."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

REPOS=Path('/home/jgett/REPOS')
ROOT=REPOS/'.work/skycache-all-targets-2026-10-27'
SOURCE=REPOS/'bazel-skycache-remote-mvp'
OLD=json.loads((REPOS/'.work/skycache-retained-chunks-2026-10-25/agent-matrix/results.json').read_text())
WS=Path(OLD['workspace'])
OUT=Path(OLD['output_base'])
JDK=REPOS/'.tools/skycache-benchmark-jdk25'
CANDIDATE=REPOS/'.work/skycache-retention-2026-10-27/build-advisory/bazel'
VANILLA=Path(OLD['binary_paths']['vanilla'])
WORKER=Path(OLD['binary_paths']['worker'])
TEMPLATE=next(r for r in OLD['runs'] if r['label']=='r1-minimal-vanilla')['argv']
STARTUP=TEMPLATE[:TEMPLATE.index('build')]
assert '--host_jvm_args=-Xmx6g' in STARTUP
STARTUP=[a.replace('--host_jvm_args=-Xmx6g','--host_jvm_args=-Xmx10g') for a in STARTUP]
COMMON=[a for a in TEMPLATE[TEMPLATE.index('build')+1:]
        if not a.startswith(('//','--profile=','--build_event_json_file=','--remote_grpc_log='))]
# The external action-cache server filled during excluded preparation. Keep its
# existing objects readable, and use an isolated shared disk cache for this cohort.
COMMON=[a for a in COMMON if not a.startswith(('--disk_cache=','--remote_upload_local_results=',
    '--bes_backend=','--bes_results_url=','--experimental_disk_cache_gc_max_size=',
    '--disk_cache_gc_max_size=','--disk_cache_gc_max_age='))]
COMMON+=['--disk_cache='+str(ROOT/'ordinary-action-disk-cache'),'--remote_upload_local_results=false',
         '--bes_backend=','--bes_results_url=','--build_event_json_file_path_conversion=false',
         '--disk_cache_gc_max_size=0','--disk_cache_gc_max_age=0']

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()

def save(path,data):
    p=Path(path)
    tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(data,indent=2)+'\n')
    tmp.replace(p)

def environment(binary):
    env=dict(os.environ,BAZEL_REAL=str(binary),BAZELISK_SKIP_WRAPPER='true',
             DD_BAZEL_REMOTE_CACHE='off',TMPDIR=str(Path(OLD['fixture_reuse']['scratch'])/'tmp'))
    for name in ['PWD','OLDPWD','XDG_CACHE_HOME']:
        env.pop(name,None)
    captured=subprocess.run([str(JDK/'bin/jfr'),'print','--json','--events',
        'jdk.InitialEnvironmentVariable',str(REPOS/'.work/skycache-preliminary-2026-10-26/diagnostic-remote.jfr')],
        check=True,capture_output=True,text=True)
    prior={e['values']['key']:e['values']['value'] for e in json.loads(captured.stdout)['recording']['events']}
    inherited={'PATH','BZLMOD_ALLOW_YANKED_VERSIONS'}
    for line in (REPOS/'.work/skycache-preliminary-2026-10-26/r1-remote.bep.json').read_text().splitlines():
        event=json.loads(line)
        for section in event.get('structuredCommandLine',{}).get('sections',[]):
            for option in section.get('optionList',{}).get('option',[]):
                if option.get('optionName') in ['repo_env','action_env','test_env']:
                    value=option.get('optionValue','')
                    if value and '=' not in value:
                        inherited.add(value)
    for name in inherited:
        if name in prior: env[name]=prior[name]
        else: env.pop(name,None)
    toolroot=ROOT/'tools'
    if toolroot.exists():
        env['PATH']=str(toolroot/'usr/bin')+':'+str(toolroot/'usr/sbin')+':'+env['PATH']
    return env

def command(label,binary,extras=(),command='build'):
    workload=json.loads((ROOT/'workload.json').read_text()) if (ROOT/'workload.json').exists() else {}
    return STARTUP+[command]+COMMON+workload.get('flags',[])+list(extras)+[
        '--build_event_json_file='+str(ROOT/(label+'.bep.json')),
        '--profile='+str(ROOT/(label+'.profile.gz')),'--']+workload.get('targets',['//...'])
