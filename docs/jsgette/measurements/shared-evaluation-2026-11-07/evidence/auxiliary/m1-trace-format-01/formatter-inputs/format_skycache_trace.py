#!/usr/bin/env python3
"""Format four new Java and three edited BUILD files under the auxiliary guard."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

import package_skycache_trace as package

WORK = Path(__file__).resolve().parent
SOURCE = WORK / 'skycache-trace-source/source'
ROOT = Path('/mnt/shared-evaluation-20261107/m1-trace-format-v1')
MANIFEST = WORK / 'trace-format-manifest.json'
require, sha = package.require, package.sha
JAVA_FILES = [package.MAIN + 'skyframe/M1TraceCollector.java', package.MAIN + 'skyframe/M1TraceModule.java',
              package.TEST + 'M1TraceCollectorTest.java', package.TEST + 'M1TraceModuleTest.java']
BUILD_FILES = [package.MAIN + 'skyframe/BUILD', package.MAIN + 'bazel/BUILD', package.TEST + 'BUILD']
FILES = JAVA_FILES + BUILD_FILES
LOCK_SHA = '9cca7899f96e81dcbd81629f52fce9f62b61413361b78d5dbccd6a8784c04f45'
MAX_JAR_BYTES = 16 * 1024**2
BUILDIFIER = {'version': '10.1.0', 'filename': 'buildifier-linux-amd64',
    'url': 'https://github.com/bazel-contrib/buildtools/releases/download/v10.1.0/buildifier-linux-amd64',
    'sha256': '31b6a8aa1e5c746696788f428729701770ad91925873d8256cb885c60e12c77e'}


def artifact_closure(lock):
    wanted, pending = set(), ['com.google.googlejavaformat:google-java-format']
    while pending:
        name = pending.pop()
        if name in wanted:
            continue
        wanted.add(name)
        pending.extend(lock['dependencies'].get(name, []))
    require(len(wanted) == 7, 'Formatter dependency closure changed')
    result = []
    for name in sorted(wanted):
        group, artifact = name.split(':')
        row = lock['artifacts'][name]
        version, digest = row['version'], row['shasums']['jar']
        filename = artifact + '-' + version + '.jar'
        result.append({'coordinate': name, 'version': version, 'filename': filename, 'sha256': digest,
            'url': 'https://repo1.maven.org/maven2/' + group.replace('.', '/') + '/' + artifact + '/' + version + '/' + filename})
    return result


def acquire(row, destination, cache, allowed_prefixes=('https://repo1.maven.org/maven2/',)):
    """Only one exact content-addressed cache file or one pinned public URL."""
    origin = cache / row['sha256'] / 'file'
    if origin.is_file():
        proof = package.build.build.copy_verified(origin, destination, row['sha256'])
        return {'method': 'verified_cache_copy', 'proof': proof}
    count = 0
    with urllib.request.urlopen(row['url'], timeout=30) as response, destination.open('xb') as writer:
        require(response.url.startswith(allowed_prefixes), 'Unexpected pinned download redirect')
        while data := response.read(1024 * 1024):
            count += len(data)
            require(count <= MAX_JAR_BYTES, 'Formatter jar exceeds bound')
            writer.write(data)
    require(sha(destination) == row['sha256'], 'Downloaded formatter jar digest mismatch')
    return {'method': 'pinned_download', 'url': row['url'], 'bytes': count, 'sha256': row['sha256']}


def formatting_diff(before, after, name):
    lines = list(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
                                    fromfile='before/' + name, tofile='formatted/' + name))
    changed = sum(line.startswith(('+', '-')) and not line.startswith(('+++', '---')) for line in lines)
    return ''.join(lines), changed


def main():
    scope = package.build.build.guard_snapshot()
    require(not ROOT.exists() and not ROOT.is_symlink() and os.path.ismount(ROOT.parent), 'Fresh mounted task root required')
    require(sha(SOURCE / 'maven_install.json') == LOCK_SHA, 'Maven lock differs')
    manifest = json.loads(MANIFEST.read_bytes())
    require(manifest['maven_lock_sha256'] == LOCK_SHA and manifest['files'] ==
            {name: package.TRACE_PINS[name] for name in FILES}, 'Formatter source/lock pins differ')
    require(manifest['buildifier'] == BUILDIFIER, 'Buildifier release pin differs')
    lock = json.loads((SOURCE / 'maven_install.json').read_bytes())
    require(manifest['jars'] == artifact_closure(lock), 'Formatter jar contract differs')
    jdk = Path(manifest['jdk']['path'])
    require(jdk.resolve().is_relative_to(ROOT.parent / 'm1-native-build-v2')
            and sha(jdk / 'bin/java') == manifest['jdk']['java_sha256']
            and sha(jdk / 'release') == manifest['jdk']['release_sha256']
            and 'jdk.compiler' in (jdk / 'release').read_text(), 'Existing full JDK identity differs')
    before = {name: sha(SOURCE / name) for name in package.TRACE_PINS}
    require(before == package.TRACE_PINS, 'Reviewed trace source changed')
    ROOT.mkdir()
    for name in ('jars', 'tools', 'tmp', 'home', 'before', 'formatted'):
        (ROOT / name).mkdir()
    report = {'schema_version': 1, 'status': 'running', 'scope_before': scope,
              'manifest': manifest, 'manifest_sha256': sha(MANIFEST), 'helper_sha256': sha(Path(__file__)),
              'source_before_sha256': before, 'artifacts': [], 'commands': []}
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(ROOT / 'home'), 'TMP': str(ROOT / 'tmp'),
           'TMPDIR': str(ROOT / 'tmp'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    try:
        classpath = []
        cache = ROOT.parent / 'm1-native-build-v2/download-cache/content_addressable/sha256'
        for row in manifest['jars']:
            destination = ROOT / 'jars' / row['filename']
            report['artifacts'].append(dict(row, **acquire(row, destination, cache)))
            classpath.append(str(destination))
        buildifier = ROOT / 'tools' / BUILDIFIER['filename']
        report['buildifier'] = acquire(BUILDIFIER, buildifier, ROOT / 'no-existing-tool-cache',
            ('https://github.com/bazel-contrib/buildtools/releases/download/',
             'https://release-assets.githubusercontent.com/'))
        buildifier.chmod(0o555)
        for name in FILES:
            raw = (SOURCE / name).read_bytes()
            for folder in ('before', 'formatted'):
                destination = ROOT / folder / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                package.write_new(destination, raw)
        paths = [str(ROOT / 'formatted' / name) for name in JAVA_FILES]
        argv = [str(jdk / 'bin/java'), '-Xmx512m', '-XX:ActiveProcessorCount=2',
                '-Djava.io.tmpdir=' + str(ROOT / 'tmp')]
        argv += ['--add-exports=jdk.compiler/com.sun.tools.javac.' + name + '=ALL-UNNAMED'
                 for name in ('api', 'file', 'parser', 'tree', 'util', 'code')]
        argv += ['-cp', ':'.join(classpath), 'com.google.googlejavaformat.java.Main']
        commands = [('java-format', argv + ['--replace'] + paths),
                    ('java-check', argv + ['--dry-run', '--set-exit-if-changed'] + paths)]
        for mode in ('fix', 'check'):
            commands.append(('buildifier-' + mode, [str(buildifier), '-mode=' + mode, '-lint=off'] +
                             [str(ROOT / 'formatted' / name) for name in BUILD_FILES]))
        for name, command in commands:
            with (ROOT / (name + '.log')).open('xb') as log:
                result = subprocess.run(command, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=120)
            report['commands'].append({'name': name, 'argv': command, 'returncode': result.returncode})
            require(result.returncode == 0, 'Formatter failed; no original source changes')
        diffs, build_changes = [], {}
        for name in FILES:
            difference, changed = formatting_diff((ROOT / 'before' / name).read_bytes(),
                                                 (ROOT / 'formatted' / name).read_bytes(), name)
            diffs.append(difference)
            if name in BUILD_FILES:
                build_changes[name] = changed
        package.write_new(ROOT / 'formatting.diff', ''.join(diffs).encode())
        report['build_changed_lines'] = build_changes
        require(max(build_changes.values()) <= 200 and sum(build_changes.values()) <= 500,
                'BUILD formatting churn exceeds review budget; originals preserved')
        require({name: sha(SOURCE / name) for name in before} == before, 'Original source changed while formatting')
        for name in FILES:
            (SOURCE / name).write_bytes((ROOT / 'formatted' / name).read_bytes())
        after = {name: sha(SOURCE / name) for name in before}
        require(all(after[name] == before[name] for name in before if name not in FILES), 'Unexpected formatted file')
        report.update(status='formatted_and_verified', source_after_sha256=after)
    except Exception as error:
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        package.document(ROOT / 'report.json', report)


if __name__ == '__main__':
    main()
