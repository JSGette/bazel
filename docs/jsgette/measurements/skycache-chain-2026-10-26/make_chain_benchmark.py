from pathlib import Path

work=Path(__file__).parent
source=(work/'skycache_validation_benchmark.py').read_text()
def replace(old,new):
    global source
    assert old in source,old
    source=source.replace(old,new)

replace('Paired benchmark of bounded final validation against the witness-reuse baseline.', 'Matched local, retained-remote and empty-retained-remote chain comparison.')
replace("ROOT = REPOS / '.work/skycache-validation-2026-10-26/paired'", "ROOT = REPOS / '.work/skycache-chain-2026-10-26/runs'")
replace("assert ROOT.parent == REPOS/'.work/skycache-validation-2026-10-26'", "assert ROOT.parent == REPOS/'.work/skycache-chain-2026-10-26'")
replace("binary = REPOS/'.work/skycache-witness-reuse-2026-10-26/bin/bazel'", "binary = REPOS/'.work/skycache-validation-2026-10-26/bin/bazel'")
replace("baseline_sha = 'ca0d089ec5876549ee7bfa550858865b7ce4c63f39797eecaa4b23bf399dc2a4'", "baseline_sha = '397ddf08c55af2d380df1b396ce4cbc96a3aa7575a0c7934a46b694bd82b52b9'")
replace("    candidate_sha = sha(candidate)", "    candidate_sha = sha(candidate)\n    assert candidate_sha == baseline_sha")
replace("    startup = base + ['--host_jvm_args=-XX:FlightRecorderOptions=stackdepth=256']", "    startup_base = base + ['--host_jvm_args=-XX:FlightRecorderOptions=stackdepth=256']\n    startup = startup_base")
start=source.index("        method='Witness-reuse baseline")
end=source.index("    save(ROOT/'results.json', results)",start)
source=source[:start]+'''        method='One binary and exact same two-publication seed for local, remote retained and remote empty-retained arms. '
               'Production hydrate reconstructs authenticated remote files for the local seed. Fresh JVM/clean graph and private cache per run; '
               'fixed source/tool/environment identity; fresh tiny genrule forces publication. Three samples per arm in rotated order. '
               'Separate JFR/method-boundary diagnostic runs and a cold-reader warmup are excluded from timing medians.')
''' + source[end:]
start=source.index("    seed_preparation = REPOS/")
end=source.index("    probe_directory=",start)
source=source[:start]+'''    seed_preparation = ROOT.parent
    preparation=json.loads((seed_preparation/'preparation.json').read_text())
    assert preparation['binary_sha256']==baseline_sha
    for arm in ['remote-warm','remote-cold']:
        shutil.copytree(seed_preparation/'metadata-cas',ROOT/('metadata-cas-'+arm),copy_function=os.link)
        shutil.copytree(seed_preparation/'catalog-seed',ROOT/('catalog-seed-'+arm))
    results['seed_preparation']=dict(path=str(seed_preparation),sha256=sha(seed_preparation/'preparation.json'),publications=preparation['publications'])
''' + source[end:]
replace('for i in range(8))','for i in range(16))')
replace('def run(label, variant, probe_index, warmup=False, primer=False, stabilization=False):','def run(label, variant, probe_index, warmup=False, primer=False, stabilization=False, diagnostic=False):')
replace("        nonlocal worker, proxy_server, proxy\n        arm, jfr = 'remote-warm', False", "        nonlocal worker, proxy_server, proxy, startup\n        arm, jfr = variant, diagnostic")
replace("        shutdown(label+'-shutdown-before')\n        env", "        shutdown(label+'-shutdown-before')\n        startup=startup_base+(['--host_jvm_args=-javaagent:'+str(ROOT.parent/'chain-probe.jar')+'='+str(ROOT/(label+'-stages.jsonl'))] if diagnostic else [])\n        env")
replace("PRIOR/'cache-primer/leased-v2'/part", "seed_preparation/'local-seed/leased-v2'/part")
start=source.index('            if not primer and not stabilization:')
end=source.index("            catalog = ROOT/",start)
source=source[:start]+'''            if arm=='remote-warm':
                shutil.copytree(seed_preparation/'retained-seed', cache/'leased-v2/remote-chunks-v1/sha256',copy_function=os.link)
''' + source[end:]
replace("        assert row['exit_code']==0 and row['outputs_match'] and not row['errors'], label", "        assert row['exit_code']==0 and row['outputs_match'] and not row['errors'], label\n        assert row['local-skycache']['writes']>0 and row['local-skycache']['reused_witness_blocks']==103,label\n        if arm=='local':\n            assert row['local-skycache']['published'],label")
replace('            if not primer and not warmup and not stabilization:', "            if arm=='remote-warm':")
start=source.index("        run('warmup-baseline'")
end=source.index("        results['status']='passed'",start)
source=source[:start]+'''        run('diagnostic-local','local',0,warmup=True,diagnostic=True)
        run('diagnostic-retained','remote-warm',1,warmup=True,diagnostic=True)
        run('warmup-cold','remote-cold',2,warmup=True)
        arms=['local','remote-warm','remote-cold','remote-cold','remote-warm','local','remote-warm','local','remote-cold']
        for i,arm in enumerate(arms,1):
            run('r'+str(i)+'-'+arm,arm,i+2)
''' + source[end:]
(work/'skycache_chain_benchmark.py').write_text(source)
compile(source,'skycache_chain_benchmark.py','exec')
print('Prepared matched chain benchmark')
