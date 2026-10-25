#!/usr/bin/env python3
"""Exercise clean-build metadata hits and lost-blob rewinding with a private HTTP cache."""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bazel', type=Path, required=True)
    parser.add_argument('--jdk', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    ws = root / 'workspace'
    ws.mkdir()
    (root / 'tmp').mkdir()
    out = root / 'output'
    cache = root / 'skycache'
    blobs, requests = {}, []

    class Cache(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def reply(self, code, data=b''):
            self.send_response(code)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_PUT(self):
            data = self.rfile.read(int(self.headers['Content-Length']))
            blobs[self.path] = data
            requests.append({'method': 'PUT', 'path': self.path, 'status': 200, 'bytes': len(data)})
            self.reply(200)

        def do_GET(self):
            data = blobs.get(self.path)
            code = 404 if data is None else 200
            requests.append({'method': 'GET', 'path': self.path, 'status': code,
                             'bytes': 0 if data is None else len(data)})
            self.reply(code, data or b'')

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Cache)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{server.server_port}'
    (ws / 'MODULE.bazel').write_text('module(name="execution_probe")\n')
    (ws / 'source.txt').write_text('source-v1\n')
    (ws / 'rules.bzl').write_text(r'''def _copy(ctx):
    out = ctx.actions.declare_file(("deep/nested/" if ctx.label.name == "middle" else "") + ctx.label.name + ".txt")
    ctx.actions.run_shell(inputs = [ctx.file.src], outputs = [out],
        command = 'cat "$1" > "$2"; echo "$3" >> "$2"',
        arguments = [ctx.file.src.path, out.path, ctx.label.name],
        execution_requirements = {"no-remote": "1"} if ctx.attr.local else {})
    return [DefaultInfo(files = depset([out]))]
copy = rule(implementation = _copy, attrs = {
    "src": attr.label(allow_single_file = True), "local": attr.bool()})
def _write(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".txt")
    ctx.actions.write(out, "internal-write\n")
    return [DefaultInfo(files = depset([out]))]
write = rule(implementation = _write)
def _link(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".txt")
    ctx.actions.symlink(output=out, target_file=ctx.file.src)
    return [DefaultInfo(files=depset([out]))]
link = rule(implementation=_link, attrs={"src": attr.label(allow_single_file=True)})
''')
    (ws / 'BUILD.bazel').write_text('''load(":rules.bzl", "copy", "write", "link")
copy(name="middle", src="source.txt")
copy(name="final", src=":middle")
copy(name="consumer", src=":middle", local=True)
write(name="internal")
link(name="alias", src=":middle")
''')
    # Keep the environment stable across all invocations and cache admissions.
    env = dict(os.environ, TMPDIR=str(root / 'tmp'))
    for key in ['PWD', 'OLDPWD', 'XDG_CACHE_HOME']:
        env.pop(key, None)
    startup = [str(args.bazel.resolve()), '--nosystem_rc', '--nohome_rc', '--noworkspace_rc',
               '--output_base=' + str(out), '--server_javabase=' + str(args.jdk.resolve()),
               '--host_jvm_args=-Xmx2g', '--max_idle_secs=120']
    protocol = {'status': 'running', 'binary': str(args.bazel),
                'binary_sha256': hashlib.sha256(args.bazel.read_bytes()).hexdigest(),
                'remote_cache': endpoint, 'runs': []}

    def save():
        (root / 'results.json').write_text(json.dumps(protocol, indent=2) + '\n')

    def call(label, argv):
        start = time.monotonic()
        with (root / (label + '.log')).open('w') as log:
            p = subprocess.run(startup + argv, cwd=ws, env=env, stdout=log,
                               stderr=subprocess.STDOUT, timeout=180)
        row = {'label': label, 'argv': startup + argv, 'seconds': time.monotonic()-start,
               'exit_code': p.returncode}
        protocol['runs'].append(row)
        save()
        assert p.returncode == 0, label
        return row

    def build(label, target='final', mode='minimal', enabled=True, accept=True):
        call(label + '-clean', ['clean', '--noasync', '--noexpunge'])
        assert not (out / 'action_cache').exists()
        assert not (out / 'execroot').exists() or not list((out / 'execroot').iterdir())
        call(label + '-graph', ['dump', '--skyframe=summary'])
        assert 'Node count: 0' in (root / (label + '-graph.log')).read_text()
        mark = len(requests)
        row = call(label, ['build', '//:' + target, '--jobs=4', '--color=no', '--curses=no',
                          '--disk_cache=', '--remote_cache=' + endpoint, '--remote_retries=0',
                          '--remote_download_outputs=' + mode, '--remote_upload_local_results',
                          '--remote_accept_cached=' + str(accept).lower(),
                          '--experimental_strict_repo_env',
                          '--experimental_local_skycache=' + (str(cache) if enabled else ''),
                          '--experimental_analysis_cache_idle_retention=0',
                          '--build_event_json_file=' + str(root / (label + '.bep.json'))])
        row['requests'] = requests[mark:]
        row['action_cache_gets'] = sum(r['method']=='GET' and '/ac/' in r['path'] for r in row['requests'])
        row['cas_gets'] = sum(r['method']=='GET' and '/cas/' in r['path'] for r in row['requests'])
        row['missing_blob_reads'] = sum(r['status']==404 and '/cas/' in r['path'] for r in row['requests'])
        output = out / ('execroot/_main/bazel-out/k8-fastbuild/bin/' + target + '.txt')
        row['output_exists'] = output.exists()
        row['contents'] = output.read_text() if output.exists() else None
        if enabled:
            shutil.copy2(out / 'local-skycache.json', root / (label + '.skycache.json'))
        print(json.dumps(row), flush=True)
        save()
        return row

    try:
        build('populate-remote', enabled=False)
        prime = build('prime-metadata')
        assert prime['action_cache_gets'] == 2
        hit = build('minimal-hit')
        assert hit['action_cache_gets'] == hit['cas_gets'] == 0, hit
        assert not hit['output_exists']
        top = build('toplevel-hit', mode='toplevel')
        assert top['action_cache_gets'] == 0 and top['cas_gets'] == 1, top
        assert top['contents'] == 'source-v1\nmiddle\nfinal\n'
        link = build('symlink-to-unmaterialized-input', target='alias')
        assert link['action_cache_gets'] == link['cas_gets'] == 0
        middle_digest = hashlib.sha256(b'source-v1\nmiddle\n').hexdigest()
        middle_key = '/cas/' + middle_digest
        assert middle_key in blobs
        del blobs[middle_key]
        absent = build('unused-missing-intermediate')
        assert absent['action_cache_gets'] == absent['cas_gets'] == 0
        recovered = build('required-missing-intermediate', target='consumer', mode='toplevel')
        assert recovered['missing_blob_reads'] >= 1, recovered
        assert recovered['contents'] == 'source-v1\nmiddle\nconsumer\n'
        assert middle_key in blobs, 'rewind did not restore the missing output'
        final_key = '/cas/' + hashlib.sha256(b'source-v1\nmiddle\nfinal\n').hexdigest()
        del blobs[final_key]
        recovered_top = build('missing-toplevel', mode='toplevel')
        assert recovered_top['missing_blob_reads'] >= 1
        assert recovered_top['contents'] == 'source-v1\nmiddle\nfinal\n'
        assert final_key in blobs
        uncached = build('remote-cache-disabled', mode='minimal', accept=False)
        assert uncached['action_cache_gets'] == 0
        assert uncached['contents'] == 'source-v1\nmiddle\nfinal\n'
        internal = build('internal-output', target='internal', mode='minimal')
        assert internal['contents'] == 'internal-write\n'
        internal = build('internal-after-clean', target='internal', mode='minimal')
        assert internal['contents'] == 'internal-write\n'
        (ws / 'source.txt').write_text('source-v2\n')
        changed = build('changed-source', mode='toplevel')
        assert changed['contents'] == 'source-v2\nmiddle\nfinal\n'
        assert changed['action_cache_gets'] >= 1
        protocol['status'] = 'passed'
    except BaseException as error:
        protocol['status'] = 'failed'
        protocol['failure'] = repr(error)
        raise
    finally:
        call('shutdown', ['shutdown'])
        server.shutdown()
        save()


if __name__ == '__main__':
    main()
