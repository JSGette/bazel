from pathlib import Path
import json
D=Path('/home/jgett/REPOS/bazel-skyframe-findings/docs/jsgette')
for name in ['skycache-v2-datadog-ssd-2026-10-25.md','skycache-v2-wsl-continuation-2026-10-24.md']:
 p=D/name;text=p.read_text()
 link='skycache-v2-fresh-wsl-2026-10-25.md'
 if link not in text:
  p.write_text(text.rstrip()+'\n\n## Fresh WSL follow-up\n\n[Completed Datadog comparison on the E: SSD]('+link+') records the authorized Ubuntu/Qwen purge, verified backups, fresh setup and 42 measured builds using Cache & Carry. The real Datadog target shows slower warm builds and higher foreground memory with SkyCache; the earlier synthetic gains do not generalize to this workload.\n')
E=D/'measurements/skycache-v2-fresh-wsl-2026-10-25'
p=E/'hardware.json';p.write_text(json.dumps(json.loads(p.read_text(encoding='utf-8-sig')),indent=2)+'\n')
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
for name in ['publish-fresh-evidence.py','link-fresh-report.py']:(E/name).write_bytes((T/'work'/name).read_bytes())
print('Historical reports linked; public Windows hardware JSON normalized to LF.')
