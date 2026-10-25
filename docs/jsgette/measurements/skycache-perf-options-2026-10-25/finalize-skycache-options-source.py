from pathlib import Path
import subprocess,json,shutil,hashlib
S=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
assert json.loads((R/'controlled/results.json').read_text())['status']=='passed'
assert json.loads((R/'profiles/results.json').read_text())['status']=='passed'
assert subprocess.check_output(['git','branch','--show-current'],cwd=S).strip()==b'jsgette/skycache-perf-options'
paths=['src/main/java/com/google/devtools/build/lib/skyframe/serialization/SharedValueDeserializationContext.java','src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/PackedFingerprintValueStore.java','src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/SegmentedFingerprintValueStore.java']
assert set(subprocess.check_output(['git','diff','--name-only'],cwd=S).decode().splitlines())==set(paths)
subprocess.run(['git','diff','--check'],cwd=S,check=True)
subprocess.run(['git','add','--',*paths],cwd=S,check=True)
subprocess.run(['git','commit','-q','-m','perf(skycache): prototype lookup reuse and array decoding'],cwd=S,check=True)
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=S).decode().strip()
tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=S).decode().strip()
r=dict(experimental_commit=head,tree=tree,branch='jsgette/skycache-perf-options',base='1aac2aa2ec6fe31af2e979558f06a3b6971c601b',promoted_to_main=False,binaries={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (R/'bin').iterdir()},tests=json.loads((R/'tests-summary.json').read_text()),note='Experimental only. Original jsgette/skycache-v2-local remains at baseline; no budget or validation semantics change.')
(R/'source-verification.json').write_text(json.dumps(r,indent=2)+'\n')
(R/'prototype.patch').write_bytes(subprocess.check_output(['git','diff',r['base'],head,'--binary'],cwd=S))
(R/'diagnostic-conditions.json').write_text(json.dumps(dict(controlled_matrix_boot_id=json.loads((R/'controlled/results.json').read_text())['boot_id'],boot_id_observed_after_profiles=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),profile_note='The WSL boot ID changed between the timing matrix and diagnostic profiles. Profile elapsed times are excluded from performance comparisons; no warm-page-cache claim for profiles. Their positive hits, proof counts, allocations and post-GC histograms are recorded separately.',excluded_attempts={'lookup-reuse':'First switch changed physical embedded_tools binding; 213 differing witnesses, 714 embedded-tool entries verified identical.','lookup-reuse-fixed':'Repository override restored admission, but FrontierNodeVersion installation MD5 still changed entry keys; zero analysis hits.','build_copy':'Build/tests passed but initial artifact copy assumed io_bazel instead of observed _main execroot. Copy repaired without rerunning build.'}),indent=2)+'\n')
print(json.dumps(r,indent=2))
