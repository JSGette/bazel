from pathlib import Path
import fcntl, hashlib, json, os, shutil, signal, socket, subprocess, time

base = Path('/home/jgett/REPOS/.work/skycache-remote-mvp')
root = base / 'comparison-warmup-smoke'
worker = base / 'build/execroot/_main/bazel-out/k8-fastbuild/bin/src/tools/remote/worker'
python = base / 'benchmark-rpc-venv/bin/python'
helper = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp/tools/skycache/remote_benchmark_warmup.py')
primer = base / 'comparison-local-remote-vanilla-1/narrow'
lock = (base.parent / 'skyframe-measurements.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
root.mkdir(exist_ok=False)
shutil.copytree(primer / 'catalog-primer-demand', root / 'catalog')
def inventory():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / 'catalog').iterdir() if p.is_file()}
before = inventory()
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
with (root / 'worker.log').open('w') as log:
    process = subprocess.Popen([str(worker), '--listen_address=127.0.0.1',
        f'--listen_port={port}', '--cas_path=' + str(primer / 'cas'),
        '--skycache_catalog_path=' + str(root / 'catalog'), '--skycache_instance_name=bench'],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.monotonic() + 45
        while 'Not starting HTTP cache server' not in (root / 'worker.log').read_text():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(.1)
        subprocess.run([str(python), str(helper), '--endpoint', f'127.0.0.1:{port}',
            '--catalog', str(root / 'catalog'), '--output', str(root / 'result.json')], check=True)
        assert inventory() == before, 'catalog bytes changed'
        print('Catalog files unchanged; helper passed', flush=True)
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
        lock.close()
