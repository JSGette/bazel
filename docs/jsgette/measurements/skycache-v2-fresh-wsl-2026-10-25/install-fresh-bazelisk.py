from pathlib import Path
import hashlib,json,os,subprocess,urllib.request
home=Path.home();root=home/'REPOS/.work/datadog-ssd-fresh-2026-10-25'
request=urllib.request.Request('https://api.github.com/repos/bazelbuild/bazelisk/releases/latest',headers={'User-Agent':'skycache-wsl-bootstrap'})
release=json.load(urllib.request.urlopen(request,timeout=30))
asset=next(a for a in release['assets'] if a['name']=='bazelisk-linux-amd64')
binary=home/'.local/bin/bazelisk';binary.parent.mkdir(parents=True,exist_ok=True)
data=urllib.request.urlopen(asset['browser_download_url'],timeout=60).read()
digest=hashlib.sha256(data).hexdigest()
assert asset.get('digest')=='sha256:'+digest, 'Official asset digest must match before execution'
binary.write_bytes(data);binary.chmod(0o755)
link=home/'.local/bin/bazel';assert not link.exists();link.symlink_to('bazelisk')
(home/'.bazelrc').write_text('# Cache & Carry, selected explicitly with --config=efcache.\nbuild:efcache --remote_cache=grpc://10.55.0.1:8080\nbuild:efcache --remote_instance_name=\nbuild:efcache --remote_timeout=120\n')
# Download/use the repository-pinned normal Bazel; experiment binaries stay explicit in the harness.
result=subprocess.run([str(binary),'--version'],cwd=home/'REPOS/datadog-agent',capture_output=True,text=True,timeout=120)
(root/'bazelisk-version.log').write_text(result.stdout+result.stderr)
assert result.returncode==0
report={'release':release['tag_name'],'asset_url':asset['browser_download_url'],'sha256':digest,'official_digest_verified':True,'version_output':result.stdout.strip()}
(root/'bazelisk-bootstrap.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
