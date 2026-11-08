#!/usr/bin/env python3
"""Post-M0 unchanged-cap source build. Invoke ONLY through run_auxiliary.ps1.

No storage resizing, cache deletion, retries, admission patch or benchmark here.
Requires a post-M0 bootstrap SHA inventory; --help performs no runtime reads.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import time

RUNTIME = Path('/mnt/shared-evaluation-20261107')
ROOT = RUNTIME / 'm1-native-build-v2'
COMMIT = '4862608d940b49293c68c014e413ecc9dc98aa8e'
TREE = 'a28c113856ec48838041bba3b9591ab9ea67e4ac'
EMBEDDED_JDK = 'ccbc15c4edbedfdcc03c2d29a2aa2c6daf9e6ffb4cda7dd5ece0fcbb37350267'
TESTS = [
    '//src/test/java/com/google/devtools/build/lib/server:ServerTests',
    '//src/test/cpp:shared_server_ownership_test',
    '//src/test/shell/integration:shared_server_test',
    '//src/test/java/com/google/devtools/build/lib/remote:SharedActionExecutionRegistryTest',
]


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def guard_snapshot():
    entries = Path('/proc/self/cgroup').read_text().splitlines()
    names = [line[3:] for line in entries if line.startswith('0::')]
    require(len(names) == 1, 'Requires cgroup v2')
    name = names[0]
    require(re.fullmatch(r'/[^\n]*/shared-eval-guard-[A-Za-z0-9-]+\.scope', name),
            'Requires the externally owned host-guard scope')
    directory = Path('/sys/fs/cgroup') / name.lstrip('/')
    counters = {key: (directory / key).read_text().strip() for key in
                ('memory.max', 'memory.swap.max', 'cpu.max', 'cpu.stat',
                 'memory.current', 'memory.peak', 'memory.events')}
    require(counters['memory.max'] == str(10240 * 1024**2), 'Expected 10 GiB scope cap')
    require(counters['memory.swap.max'] == '0', 'Expected no scope swap')
    quota, period = counters['cpu.max'].split()
    require(quota != 'max' and int(quota) == 6 * int(period), 'Expected six-CPU quota')
    return {'name': name, 'counters': counters}


def copy_verified(source, target, expected, *, executable=False):
    """One bounded source read; advise only that exact read file after copying."""
    require(re.fullmatch('[0-9a-f]{64}', expected), 'Invalid SHA256')
    require(source.resolve() == source and not source.is_symlink(), 'Source alias rejected')
    require(not target.exists(), 'Destination already exists')
    digest = hashlib.sha256()
    advised = False
    with source.open('rb') as reader:
        before = os.fstat(reader.fileno())
        require(stat.S_ISREG(before.st_mode), 'Source must be a regular file')
        try:
            with target.open('xb') as writer:
                while block := reader.read(1024 * 1024):
                    digest.update(block)
                    writer.write(block)
            after = os.fstat(reader.fileno())
            require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                    'Source changed during copy')
            require(digest.hexdigest() == expected, 'Source digest mismatch: ' + str(source))
            require(target.stat().st_size == before.st_size and sha(target) == expected,
                    'Copied digest mismatch')
        finally:
            os.posix_fadvise(reader.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            advised = True
    target.chmod(0o555 if executable else 0o444)
    return {'source': str(source), 'destination': str(target), 'sha256': expected,
            'bytes': before.st_size, 'source_fadv_dontneed': advised,
            'advice_is_not_guaranteed_eviction': True}


def download_entries(cache, max_files, max_bytes):
    """Only sha256 artifact + empty canonical-ID markers; never enter contents."""
    directory = cache / 'content_addressable' / 'sha256'
    require(directory.resolve() == directory and directory.is_dir(), 'Seed cache alias/missing')
    result, total = [], 0
    with os.scandir(directory) as entries:
        for entry in entries:
            require(re.fullmatch('[0-9a-f]{64}', entry.name) and
                    entry.is_dir(follow_symlinks=False), 'Unexpected download-cache entry')
            bucket = Path(entry.path)
            with os.scandir(bucket) as members:
                for member in members:
                    require(member.is_file(follow_symlinks=False), 'Invalid download-cache member')
                    size = member.stat(follow_symlinks=False).st_size
                    if member.name == 'file':
                        digest = entry.name
                    else:
                        require(re.fullmatch('id-[0-9a-f]{64}', member.name) and size == 0,
                                'Unexpected canonical-ID marker')
                        digest = hashlib.sha256(b'').hexdigest()
                    total += size
                    result.append((digest, Path(member.path)))
                    require(len(result) <= max_files and total <= max_bytes, 'Seed budget exceeded')
    require(any(digest == EMBEDDED_JDK and path.name == 'file' for digest, path in result),
            'Pinned JDK27 archive absent')
    return sorted(result)


def extract_archive(archive, source):
    """Pinned git archive: plain files/directories and internal relative symlinks only."""
    with tarfile.open(archive, 'r:') as stream:
        members = stream.getmembers()
        seen = set()
        for member in members:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and '..' not in name.parts and
                    member.name not in seen, 'Unsafe/duplicate archive member')
            seen.add(member.name)
            require(member.isfile() or member.isdir() or member.issym(), 'Unsupported archive type')
            if member.issym():
                target = source / member.name
                link = PurePosixPath(member.linkname)
                require(not link.is_absolute() and
                        (target.parent / member.linkname).resolve().is_relative_to(source),
                        'Escaping source symlink')
        # Do not permit later files below any archive symlink, even an internal one.
        links = {PurePosixPath(m.name) for m in members if m.issym()}
        for member in members:
            require(not any(parent in links for parent in PurePosixPath(member.name).parents),
                    'Archive member below symlink')
        stream.extractall(source, members=members, filter='fully_trusted')


def make_rc(text):
    line = 'try-import %workspace%/user.bazelrc'
    require(text.splitlines().count(line) == 1, 'Expected exactly one optional user rc import')
    result = '\n'.join(row for row in text.splitlines() if row != line) + '\n'
    require(not any(re.match(r'\s*(try-)?import\s', row) for row in result.splitlines()),
            'Unexpected extra rc import')
    return result


def source_git_command(directory):
    # Windows worktree .git pointers contain Windows paths that WSL Git cannot use.
    # Read objects directly from the known main repository; no worktree discovery.
    return ['git', '--git-dir=' + str(directory)]


def commands(root):
    temporary = str(root / 'tmp')
    startup = [str(root / 'tools/bazel-9.3.1'), '--nosystem_rc', '--nohome_rc',
               '--noworkspace_rc', '--bazelrc=' + str(root / 'build.bazelrc'),
               '--output_user_root=' + str(root / 'build-user'),
               '--output_base=' + str(root / 'build-output'),
               '--host_jvm_args=-Xmx3g', '--host_jvm_args=-XX:ActiveProcessorCount=2',
               '--host_jvm_args=-Djava.io.tmpdir=' + temporary]
    options = ['--jobs=2', '--local_resources=cpu=2', '--local_resources=memory=4096',
               '--repository_cache=' + str(root / 'download-cache'),
               '--repo_contents_cache=' + str(root / 'repo-contents'), '--lockfile_mode=error',
               '--sandbox_base=' + str(root / 'sandbox'),
               '--jvmopt=-Djava.io.tmpdir=' + temporary,
               '--host_jvmopt=-Djava.io.tmpdir=' + temporary]
    for kind in ('repo_env', 'action_env', 'test_env'):
        for name in ('TMP', 'TMPDIR'):
            options.append('--' + kind + '=' + name + '=' + temporary)
    extra_rc = '\n'.join(['startup --host_jvm_args=-Xmx768m',
                          'startup --host_jvm_args=-XX:ActiveProcessorCount=2',
                          'startup --host_jvm_args=-Djava.io.tmpdir=' + temporary])
    test_options = ['--test_output=errors', '--local_test_jobs=1',
                    '--test_tmpdir=' + str(root / 'test-tmp'),
                    '--test_env=REPOSITORY_CACHE=' + str(root / 'download-cache'),
                    '--test_env=TEST_INSTALL_BASE=' + str(root / 'test-install/install'),
                    '--sandbox_writable_path=' + str(root / 'download-cache'),
                    '--sandbox_writable_path=' + str(root / 'test-install'),
                    '--test_env=EXTRA_BAZELRC=' + extra_rc]
    return {
        'version': startup + ['version', '--gnu_format'],
        'build': startup + ['build', '//src:bazel-dev'] + options +
                 ['--build_event_json_file=' + str(root / 'build.bep.jsonl')],
        'tests': startup + ['test'] + TESTS + options + test_options +
                 ['--build_event_json_file=' + str(root / 'tests.bep.jsonl')],
        'shutdown': startup + ['shutdown'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-git-dir', type=Path, required=True)
    parser.add_argument('--source-commit', default=COMMIT)
    parser.add_argument('--source-tree', default=TREE)
    parser.add_argument('--bootstrap', type=Path,
                        default=Path('/home/jgett/bazel-multiworktree-20261031/local-tools/bazel-9.3.1'))
    parser.add_argument('--bootstrap-sha256', required=True)
    parser.add_argument('--seed-cache', type=Path,
                        default=Path('/home/jgett/bazel-multiworktree-20261031/control/repository-cache'))
    parser.add_argument('--minimum-free-gib', type=int, default=60)
    parser.add_argument('--maximum-seed-files', type=int, default=20000)
    parser.add_argument('--maximum-seed-gib', type=int, default=20)
    args = parser.parse_args()
    require(re.fullmatch('[0-9a-f]{40}', args.source_commit) and
            re.fullmatch('[0-9a-f]{40}', args.source_tree), 'Requires full commit and tree SHA1')
    require(re.fullmatch('[0-9a-f]{64}', args.bootstrap_sha256), 'Requires bootstrap SHA256')
    require(min(args.minimum_free_gib, args.maximum_seed_files, args.maximum_seed_gib) > 0,
            'Budgets must be positive')
    scope = guard_snapshot()
    require(RUNTIME.resolve() == RUNTIME and os.path.ismount(RUNTIME), 'Task volume not mounted')
    require(not ROOT.exists() and not ROOT.is_symlink(), 'Build root already exists; no resume/overwrite')
    require(shutil.disk_usage(RUNTIME).free >= args.minimum_free_gib * 1024**3,
            'Insufficient task-volume space; resize outside this script')
    require(args.source_git_dir.resolve() == args.source_git_dir and
            args.source_git_dir.is_dir() and not args.source_git_dir.is_relative_to(RUNTIME),
            'Source Git directory must be an existing non-aliased directory outside R')
    ROOT.mkdir()
    for directory in ('source', 'tools', 'tmp', 'home', 'cache', 'config', 'data', 'sandbox',
                      'download-cache', 'repo-contents', 'test-tmp', 'test-install', 'candidate'):
        (ROOT / directory).mkdir()
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(ROOT / 'home'),
           'TMP': str(ROOT / 'tmp'), 'TMPDIR': str(ROOT / 'tmp'), 'LANG': 'C.UTF-8',
           'LC_ALL': 'C.UTF-8', 'XDG_CACHE_HOME': str(ROOT / 'cache'),
           'XDG_CONFIG_HOME': str(ROOT / 'config'), 'XDG_DATA_HOME': str(ROOT / 'data'),
           'PYTHONDONTWRITEBYTECODE': '1',
           'JAVA_TOOL_OPTIONS': '-Djava.io.tmpdir=' + str(ROOT / 'tmp')}
    report = {'schema_version': 1, 'status': 'running', 'root': str(ROOT),
              'launcher_sha256': sha(Path(__file__).resolve()), 'scope_before': scope,
              'commands': [], 'environment': env, 'started_unix': time.time(),
              'source_git_dir': str(args.source_git_dir),
              'source_commit': args.source_commit, 'source_tree': args.source_tree,
              'unchanged_admission_cap': 2, 'embedded_jdk_sha256': EMBEDDED_JDK,
              'compilation_jdk': 'remotejdk_25 selected by pinned rc and MODULE.bazel.lock',
              'scope_note': 'Build qualification only; not an M1 benchmark or gate verdict.'}
    ready = False

    def run(name, argv, timeout=None):
        entry = {'name': name, 'argv': argv, 'started_unix': time.time()}
        report['commands'].append(entry)
        save(ROOT / 'report.json', report)
        with (ROOT / (name + '.log')).open('xb') as log:
            result = subprocess.run(argv, cwd=ROOT / 'source', env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        entry.update(returncode=result.returncode, finished_unix=time.time())
        save(ROOT / 'report.json', report)
        require(result.returncode == 0, name + ' failed; see its preserved log')

    try:
        git = source_git_command(args.source_git_dir)
        tree = subprocess.check_output(git + ['rev-parse', args.source_commit + '^{tree}'],
                                       env=env, text=True).strip()
        require(tree == args.source_tree, 'Source tree pin mismatch')
        archive = ROOT / 'source.tar'
        run('source-archive', git + ['archive', '--format=tar', '--output=' + str(archive), args.source_commit])
        report['source_archive_sha256'] = sha(archive)
        extract_archive(archive, ROOT / 'source')
        require((ROOT / 'source/.bazelversion').read_text().strip() == '9.3.1', 'Bootstrap version pin changed')
        server = (ROOT / 'source/src/main/java/com/google/devtools/build/lib/server/CommandServer.java').read_text()
        require('sharedEngines.size() + failedEngines.size() >= 2' in server,
                'Source does not retain the unchanged two-engine admission condition')
        repositories = (ROOT / 'source/repositories.bzl').read_text()
        require('sha256-zLwVxO2+39zAPC0poqosba+eb/tM2n3V7OD8uzc1Amc=' in repositories and
                'zulu27.28.101-ca-jdk27.0.0-linux_x64.tar.gz' in repositories,
                'Embedded JDK27 pin changed')
        rc = ROOT / 'source/.bazelrc'
        (ROOT / 'build.bazelrc').write_text(make_rc(rc.read_text()))
        report['configuration_sha256'] = {name: sha(ROOT / 'source' / name) for name in
                                          ('.bazelrc', '.bazelversion', 'MODULE.bazel',
                                           'MODULE.bazel.lock', 'maven_install.json',
                                           'repositories.bzl', 'bazel_downloader.cfg')}
        report['generated_rc_sha256'] = sha(ROOT / 'build.bazelrc')
        report['rc_transformation'] = 'Removed only try-import %workspace%/user.bazelrc'
        report['bootstrap'] = copy_verified(args.bootstrap, ROOT / 'tools/bazel-9.3.1',
                                            args.bootstrap_sha256, executable=True)
        entries = download_entries(args.seed_cache, args.maximum_seed_files,
                                   args.maximum_seed_gib * 1024**3)
        seed = {'source': str(args.seed_cache),
                'scope': 'sha256 artifacts and empty canonical-ID markers only; no contents',
                'members': [], 'complete': False}
        save(ROOT / 'seed-manifest.json', seed)
        with (ROOT / 'seed-members.jsonl').open('x') as journal:
            for digest, path in entries:
                destination = ROOT / 'download-cache' / path.relative_to(args.seed_cache)
                destination.parent.mkdir(parents=True, exist_ok=True)
                member = copy_verified(path, destination, digest)
                seed['members'].append(member)
                journal.write(json.dumps(member) + '\n')
                journal.flush()
        seed.update(complete=True, files=len(entries), bytes=sum(row['bytes'] for row in seed['members']))
        save(ROOT / 'seed-manifest.json', seed)
        report['seed_manifest_sha256'] = sha(ROOT / 'seed-manifest.json')
        report['tool_versions'] = {}
        for tool, flag in [('gcc', '--version'), ('g++', '--version'), ('python3', '--version'),
                           ('git', '--version'), ('unzip', '-v'), ('zip', '-v')]:
            output = subprocess.check_output([tool, flag], cwd=ROOT / 'source', env=env,
                                             text=True, stderr=subprocess.STDOUT)
            report['tool_versions'][tool] = output.splitlines()[:3]
        command = commands(ROOT)
        ready = True
        run('version', command['version'], 60)
        require('bazel 9.3.1' in (ROOT / 'version.log').read_text(), 'Unexpected bootstrap version output')
        run('build', command['build'])
        run('tests', command['tests'])
        built = (ROOT / 'source/bazel-bin/src/bazel-dev').resolve()
        require(built.is_relative_to(ROOT / 'build-output'), 'Candidate resolves outside build output')
        report['candidate'] = copy_verified(built, ROOT / 'candidate/bazel-native-cap2',
                                            sha(built), executable=True)
        report['status'] = 'build_and_focused_tests_passed_no_gate_verdict'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if ready:
            try:
                run('shutdown', commands(ROOT)['shutdown'], 120)
            except Exception as error:
                report['status'] = 'failed'
                report['shutdown_error'] = str(error)
        report['finished_unix'] = time.time()
        try:
            report['scope_after'] = guard_snapshot()
            report['runtime_free_after'] = shutil.disk_usage(RUNTIME).free
        except Exception as error:
            report['final_snapshot_error'] = str(error)
        save(ROOT / 'report.json', report)
    require(report['status'] == 'build_and_focused_tests_passed_no_gate_verdict', 'Build/cleanup failed')


if __name__ == '__main__':
    main()
