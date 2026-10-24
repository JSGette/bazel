#!/usr/bin/env bash
set -euo pipefail
cd /home/jgett/REPOS/bazel-skycache-v2
python3 - <<'PY'
from tools.skycache.storage_preflight import check_storage
from pathlib import Path
import json, platform
report = check_storage(['/mnt/skycache-v2-local'])
report['platform'] = platform.platform()
report['source_base'] = '6f451b2dc6f783dda879ff3c7ed3fc8146d0f410'
Path('/home/jgett/REPOS/.work/skycache-v2-local/storage-preflight.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
PY
mkdir -p /mnt/skycache-v2-local/tmp
export TMPDIR=/mnt/skycache-v2-local/tmp
/home/jgett/REPOS/.tools/bazel --output_user_root=/mnt/skycache-v2-local/bazel-user-root --output_base=/mnt/skycache-v2-local/build-output --host_jvm_args=-Xmx5g test //src:bazel-dev //src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:all --jobs=6 --local_resources=memory=8192 --test_output=errors --color=no --curses=no > /mnt/skycache-v2-local/build-tests.log 2>&1
