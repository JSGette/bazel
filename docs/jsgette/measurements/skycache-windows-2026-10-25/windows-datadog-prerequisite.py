from pathlib import Path
import os
from importlib.machinery import SourceFileLoader
p=SourceFileLoader('probe',str(Path(__file__).with_name('windows-probe.py'))).load_module()
binary = next(Path('E:/scw/bazelisk/downloads/sha256').glob('*/bin/bazel.exe'))
env = dict(os.environ, BAZEL_REAL=str(binary), BAZELISK_SKIP_WRAPPER='true', DD_BAZEL_REMOTE_CACHE='off',
           XDG_CACHE_HOME='E:/scw/xdg', TEMP='E:/scw/tmp', TMP='E:/scw/tmp')
Path(env['XDG_CACHE_HOME']).mkdir(exist_ok=True)
p.run('datadog-wrapper-prerequisite', ['E:/scw/dd/tools/bazel.bat', '--output_base=E:/scw/do',
     'build', '//cmd/dogstatsd:dogstatsd', '--remote_cache=grpc://10.55.0.1:8080',
     '--bes_backend=grpc://10.55.0.1:8080', '--bes_results_url=http://192.168.2.71:8088/invocation/'],
     cwd='E:/scw/dd', env=env)
