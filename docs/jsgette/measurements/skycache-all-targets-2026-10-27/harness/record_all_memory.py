from skycache_all_common import *
import time

config=Path('/mnt/c/Users/jgett/.wslconfig')
backup=Path('/mnt/c/Users/jgett/.wslconfig.skycache-backup-20261027-104052')
mem={k:v.strip() for k,v in (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())}
assert 19*1024*1024<int(mem['MemTotal'].split()[0])<21*1024*1024
assert 'memory=20GB' in config.read_text()
save(ROOT/'wsl-memory-change.json',dict(epoch=time.time(),reason='User requested more WSL RAM',
    host_physical_memory_bytes=33990049792,old_limit='WSL default, approximately 16 GiB',
    config_path=str(config),backup_path=str(backup),before_config=backup.read_text(),
    after_config=config.read_text(),linux_memory=mem,java_heap_all_measured_arms='10 GiB',
    activation='WSL automatically stopped after the checkpointed seed process exited; the next Ubuntu launch applied the new limit.'))
print(json.dumps(dict(memory={k:mem[k] for k in ['MemTotal','MemAvailable','SwapTotal']},
    checkpoint=json.loads((ROOT/'cache-prime-4-checkpoint.json').read_text()),
    prepared_directories={p.name:p.exists() for p in [ROOT/'metadata-cas-primer',ROOT/'catalog-primer',ROOT/'metadata-cas-seed',ROOT/'local-seed']})))
