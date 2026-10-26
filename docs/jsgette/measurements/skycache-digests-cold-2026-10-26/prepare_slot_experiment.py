from pathlib import Path
import subprocess,sys
work=Path(__file__).parent
s=(work/'build_digest_agent.py').read_text().replace("2026-10-26/agent'", "2026-10-26/agent-slots'").replace("build-candidate/bazel", "build-slots-v2/bazel")
(work/'build_slot_agent.py').write_text(s)
subprocess.run([sys.executable,str(work/'build_slot_agent.py')],check=True)
s=(work/'skycache_digest_benchmark.py').read_text().replace('agent/agent.jar','agent-slots/agent.jar')
s=s.replace("'cold':[('local','channels'),('remote-cold','channels'),('remote-cold','scratch')]", "'cold':[('remote-cold','channels'),('remote-cold','scratch')]")
s=s.replace("'cold':[0,1,2,2,1,0,1,0,2]", "'cold':[0,1,1,0,0,1]")
(work/'skycache_slots_benchmark.py').write_text(s)
compile(s,'skycache_slots_benchmark.py','exec')
