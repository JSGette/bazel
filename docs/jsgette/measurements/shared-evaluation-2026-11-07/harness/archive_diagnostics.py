"""Archive explicit diagnostic evidence directories, never output/cache trees."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('arm', choices=['off', 'on'])
parser.add_argument('destination', type=Path)
args = parser.parse_args()
root = Path('/mnt/shared-evaluation-20261107/m0-diagnostics-v1') / args.arm
report = json.loads((root / 'report.json').read_text())
directories = ['preparation', 'restart', 'warm', 'restart-shutdown', 'final-shutdown',
               'heap-after-gc', 'skyvalue-memory', 'restart-jfr-summary',
               'restart-jfr-decoded', 'warm-jfr-summary', 'warm-jfr-decoded']
files = set(root.glob('*.json'))
for name in directories:
    directory = root / name
    if directory.is_symlink():
        raise ValueError('Symlink evidence directory')
    if directory.exists():
        for path in directory.iterdir():
            if path.is_dir() and path.name == 'behavior' and name in ('preparation', 'restart', 'warm'):
                if path.is_symlink() or (path / 'out').is_symlink():
                    raise ValueError('Redirected behavior evidence')
                if sorted(p.name for p in path.iterdir()) != ['out', 'switch.yaml']:
                    raise ValueError('Unexpected behavior evidence')
                if sorted(p.name for p in (path / 'out').iterdir()) != ['switch.json']:
                    raise ValueError('Unexpected behavior output')
                files.update([path / 'switch.yaml', path / 'out/switch.json'])
                continue
            if not path.is_file():
                raise ValueError('Unexpected nested evidence path: ' + str(path))
            files.add(path)
members = []
for path in sorted(files):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Evidence escaped diagnostic root')
    size = path.stat().st_size
    if size > 64 * 1024**2:
        raise ValueError('Unexpected oversized diagnostic file')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    members.append({'path': path.relative_to(root).as_posix(), 'bytes': size, 'sha256': digest})
if sum(row['bytes'] for row in members) > 256 * 1024**2:
    raise ValueError('Unexpected diagnostic total size')
args.destination.parent.mkdir(parents=True, exist_ok=True)
with args.destination.open('xb') as target:
    with gzip.GzipFile(fileobj=target, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|') as archive:
            for row in members:
                archive.add(root / row['path'], arcname=row['path'], recursive=False)
with tarfile.open(args.destination) as archive:
    if archive.getnames() != [row['path'] for row in members]:
        raise ValueError('Archive member mismatch')
    for row in members:
        data = archive.extractfile(row['path']).read()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('Archive byte verification failed')
with args.destination.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
manifest = {'run': str(root), 'passed': report['passed'], 'diagnostic_only': True,
            'archive_sha256': digest, 'archive_bytes': args.destination.stat().st_size,
            'members': members, 'scope': 'Explicit diagnostic evidence only; excludes output, SkyCache, temp and Java directories'}
with args.destination.with_suffix(args.destination.suffix + '.json').open('x') as out:
    json.dump(manifest, out, indent=2)
    out.write('\n')
print(json.dumps({key: value for key, value in manifest.items() if key != 'members'}))
