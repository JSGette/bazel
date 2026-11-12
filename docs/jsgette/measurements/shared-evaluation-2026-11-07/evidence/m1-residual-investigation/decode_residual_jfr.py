"""Decode only two pinned historical recordings, under the existing task guard."""
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time

from analyze_m0_diagnostics import validated_archive

W = Path(__file__).resolve().parent
E = W / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
P = E / 'm1-residual-investigation-protocol.md'
PROTOCOL_SHA = 'b4bf447302e4e4a7cf39f6b7940026f26694d4cc6927d808bdd8aa315e2c66fa'
TOOL = Path('/mnt/shared-evaluation-20261107/install/install/03726f21798d3df246f5b7cb1702ec00/embedded_tools/jdk/bin/jfr')
TOOL_SHA = 'c313fd7830d5acd4ae73749bbbb48a5705e1c3bbb41957d4c3223eb636417d5a'
OUT = W / 'm1-residual-analysis-01'
TMP = Path('/mnt/shared-evaluation-20261107/m1-residual-decoder-01')
PINS = {'off': '58a49f20aed91d0576e3780d8f8170cdc1b12d71e9a1b169c20c12c3c7061b87',
        'on': 'f0fa344da92e7eeffaf596909f50a9d8bd9b6aee5e0c972e71add156d8202676'}

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def file_record(p):
    return {'path': str(p), 'sha256': sha(p), 'bytes': p.stat().st_size}

def limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))

assert sha(P) == PROTOCOL_SHA and sha(TOOL) == TOOL_SHA
assert not OUT.exists() and not TMP.exists()
OUT.mkdir()
TMP.mkdir()
report = {'schema_version': 1, 'kind': 'archived-m0-restart-fullstack-decode',
          'protocol_path': str(P), 'protocol_sha256': PROTOCOL_SHA,
          'tool': file_record(TOOL), 'decoder_sha256': sha(Path(__file__)),
          'archive_validator_sha256': sha(W / 'analyze_m0_diagnostics.py'),
          'scope': Path('/proc/self/cgroup').read_text(), 'rows': [], 'passed': False,
          'diagnostic_only': True, 'milestone_verdict': None}
assert 'shared-eval' in report['scope'], 'Must execute under task-owned guard'
started = time.monotonic()
try:
    for arm, pin in PINS.items():
        archive = E / 'evidence/auxiliary' / f'm0-diagnostic-{arm}-01/run.tar.gz'
        data, verification = validated_archive(archive, pin)
        raw = data['restart/cpu.jfr']
        assert len(raw) < 1024**2
        raw_path = OUT / f'{arm}-restart.jfr'
        with raw_path.open('xb') as f:
            f.write(raw)
        argv = [str(TOOL), '-J-Xmx512m', '-J-XX:ActiveProcessorCount=2',
                '-J-Djava.io.tmpdir=' + str(TMP), 'print', '--json', '--stack-depth', '2048',
                '--events', 'jdk.ExecutionSample,jdk.NativeMethodSample', str(raw_path)]
        row = {'arm': arm, 'archive_path': str(archive), 'archive_sha256': pin,
               'archive_verification': verification, 'raw_member': 'restart/cpu.jfr',
               'raw_sha256': hashlib.sha256(raw).hexdigest(), 'raw': file_record(raw_path),
               'original_decoded_member': 'restart-jfr-decoded/stdout.log',
               'summary_member': 'restart-jfr-summary/stdout.log',
               'original_command': json.loads(data['restart-jfr-decoded/command.json']),
               'historical_report_sha256': hashlib.sha256(data['report.json']).hexdigest()}
        stdout = OUT / f'{arm}-restart-full.json'
        stderr = OUT / f'{arm}-restart-stderr.log'
        env = dict(os.environ)
        for key in ('JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS'):
            env.pop(key, None)
        begin = time.monotonic()
        row['command'] = {'argv': argv, 'exit_code': None, 'wall_seconds': None}
        report['rows'].append(row)
        try:
            with stdout.open('xb') as out, stderr.open('xb') as err:
                p = subprocess.run(argv, stdout=out, stderr=err, env=env, preexec_fn=limits,
                                   timeout=max(1, 570 - (time.monotonic() - started)))
            row['command']['exit_code'] = p.returncode
        except Exception as exc:
            row['command']['error'] = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            row['command']['wall_seconds'] = time.monotonic() - begin
            for name, path in (('decoded', stdout), ('stderr', stderr)):
                if path.exists():
                    row[name] = file_record(path)
        assert p.returncode == 0, f'{arm} decode failed; see stderr'
        assert 0 < stdout.stat().st_size <= 128 * 1024**2 and stderr.stat().st_size <= 1024**2
    assert sha(TOOL) == TOOL_SHA and sha(P) == PROTOCOL_SHA
    report['passed'] = True
finally:
    report['wall_seconds'] = time.monotonic() - started
    with (OUT / 'decode-report.json').open('x') as f:
        json.dump(report, f, indent=2)
        f.write('\n')
print(json.dumps({'passed': report['passed'], 'rows': len(report['rows']), 'report': str(OUT / 'decode-report.json')}))
