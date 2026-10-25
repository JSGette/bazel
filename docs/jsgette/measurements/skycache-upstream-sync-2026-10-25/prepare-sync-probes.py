from pathlib import Path
R=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
RUN=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
s=(R/'tools/skycache/local_worktree_probe.py').read_text()
s=s.replace('from storage_preflight import check_storage', 'import sys\nsys.path.insert(0, '+repr(str(R/'tools/skycache'))+')\nfrom storage_preflight import check_storage')
s=s.replace('cache/leased-v1/objects','cache/leased-v2/objects/segmented-v1')
s=s.replace('.rglob("data.pack")','.rglob("segment-*.pack")').replace('.rglob("index")','.rglob("segment-*.index")')
s=s.replace('(path.parent / "index").read_bytes()', 'path.with_suffix(".index").read_bytes()')
s=s.replace('"--experimental_active_directories=app", "--lockfile_mode=off",', '''"--lockfile_mode=off", "--disk_cache=", "--remote_cache=grpc://10.55.0.1:8080",
            "--remote_max_connections=1", "--remote_max_concurrency_per_connection=16",
            "--remote_download_outputs=toplevel", "--remote_upload_local_results",
            "--bes_backend=grpc://10.55.0.1:8080",''')
(RUN/'local-worktree-probe-segmented.py').write_text(s)
(RUN/'local-execution-probe.py').write_bytes((R/'tools/skycache/local_execution_probe.py').read_bytes())
print('Prepared segmented worktree probe and isolated missing-CAS fault probe.')
