from pathlib import Path

work=Path(__file__).parent
s=(work/'skycache_chain_benchmark.py').read_text().replace('.work/skycache-chain-2026-10-26/runs','.work/skycache-chain-2026-10-26/direct-runs')
s=s.replace("arg=f'--experimental_remote_skycache=grpc://127.0.0.1:{proxy_port}'", "arg=f'--experimental_remote_skycache=grpc://127.0.0.1:{port}'")
s=s.replace("'Separate JFR/method-boundary diagnostic runs and a cold-reader warmup are excluded from timing medians.'", "'Remote timed commands connect directly to the worker; the created proxy is bypassed and records no RPCs. Local warmup excluded.'")
a=s.index("        run('diagnostic-local'")
b=s.index("        results['status']='passed'",a)
s=s[:a]+'''        run('warmup-local','local',0,warmup=True)
        arms=['local','remote-warm','remote-cold','remote-cold','remote-warm','local','remote-warm','local','remote-cold']
        for i,arm in enumerate(arms,1):
            run('r'+str(i)+'-'+arm,arm,i+2)
''' +s[b:]
(work/'skycache_chain_direct.py').write_text(s)
print('Prepared direct-endpoint matched comparison')
