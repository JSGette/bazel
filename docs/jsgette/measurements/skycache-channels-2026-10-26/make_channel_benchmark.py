from pathlib import Path

work=Path(__file__).parent
s=(work/'skycache_chain_direct.py').read_text()
def replace(old,new):
 global s
 assert old in s,old
 s=s.replace(old,new)

replace("ROOT = REPOS / '.work/skycache-chain-2026-10-26/direct-runs'", "ROOT = REPOS / '.work/skycache-channels-2026-10-26/warm-controlled'")
replace("    parser.add_argument('--candidate', type=Path, required=True)",
 "    parser.add_argument('--candidate', type=Path, required=True)\n    parser.add_argument('--candidate128', type=Path, required=True)\n    parser.add_argument('--cold', action='store_true')")
replace("assert ROOT.parent == REPOS/'.work/skycache-chain-2026-10-26'", "assert ROOT.parent == REPOS/'.work/skycache-channels-2026-10-26'")
replace('    assert candidate_sha == baseline_sha', '''    candidate128=args.candidate128.resolve(strict=True)
    executables={'baseline':binary,'channels128':candidate128,'channels256':candidate}
    hashes={name:sha(path) for name,path in executables.items()}
    assert candidate_sha==json.loads((candidate.parent/'result.json').read_text())['binary_sha256']
    assert hashes['channels128']==json.loads((candidate128.parent/'result.json').read_text())['binary_sha256']''')
replace('        candidate_binary_sha256=candidate_sha, candidate_binary=str(candidate),',
 '        candidate_binary_sha256=candidate_sha, candidate_binary=str(candidate),\n        executables={name:str(path) for name,path in executables.items()},executable_sha256=hashes,')
replace("    seed_preparation = ROOT.parent", "    seed_preparation = REPOS/'.work/skycache-chain-2026-10-26'")
replace("def run(label, variant, probe_index, warmup=False, primer=False, stabilization=False, diagnostic=False):", "def run(label, variant, probe_index, warmup=False, primer=False, stabilization=False, diagnostic=False, implementation='baseline'):")
replace('        row = dict(label=label, arm=arm, variant=variant, warmup=warmup, primer=primer,',
 '        row = dict(label=label, arm=arm, variant=variant, implementation=implementation, warmup=warmup, primer=primer,')
replace("        env['BAZEL_REAL'] = str(binary if variant=='baseline' else candidate)","        env['BAZEL_REAL'] = str(binary)")
replace("        startup=startup_base+(['--host_jvm_args=-javaagent:'+str(ROOT.parent/'chain-probe.jar')+'='+str(ROOT/(label+'-stages.jsonl'))] if diagnostic else [])",
 "        startup=startup_base+['--host_jvm_args=-javaagent:'+str(ROOT.parent/'agent/agent.jar')+'='+implementation]")
replace('        executables={name:str(path) for name,path in executables.items()},executable_sha256=hashes,',
 '        class_source_binaries={name:str(path) for name,path in executables.items()},executable_sha256=hashes,\n        runtime_binary=str(binary),runtime_binary_sha256=baseline_sha,agent_sha256=sha(ROOT.parent/\'agent/agent.jar\'),')
replace("        assert row['exit_code']==0 and row['outputs_match'] and not row['errors'], label", "        assert row['exit_code']==0 and row['outputs_match'] and not row['errors'], label\n        assert not row.get('rpc'), 'timed command must bypass proxy'\n        if arm!='local' and implementation!='baseline':\n            stats=row['remote-skycache']\n            assert stats['cached_channel_capacity']==int(implementation.removeprefix('channels'))\n            assert stats['cached_channel_peak']<=stats['cached_channel_capacity']+stats['max_concurrency']")
replace("            results['candidate_binary_unchanged']=sha(candidate)==candidate_sha", "            results['candidate_binary_unchanged']=sha(candidate)==candidate_sha\n            results['all_binaries_unchanged']=all(sha(path)==hashes[name] for name,path in executables.items())")
a=s.index("        run('warmup-local'")
b=s.index("        results['status']='passed'",a)
s=s[:a]+'''        run('warmup-local','local',0,warmup=True)
        for i,name in enumerate(['baseline','channels128','channels256'],1):
            run('warmup-'+name,'remote-cold' if args.cold else 'remote-warm',i,warmup=True,implementation=name)
        if args.cold:
            order=['baseline','channels128','channels256','channels256','channels128','baseline','channels128','baseline','channels256']
            for i,name in enumerate(order,4):
                run('r'+str(i-3)+'-'+name,'remote-cold',i,implementation=name)
        else:
            order=['local','baseline','channels128','channels256','channels256','channels128','baseline','local','channels128','local','channels256','baseline']
            for i,name in enumerate(order,4):
                run('r'+str(i-3)+'-'+name,'local' if name=='local' else 'remote-warm',i,implementation='baseline' if name=='local' else name)
''' + s[b:]
replace("        method='One binary and exact same two-publication seed for local, remote retained and remote empty-retained arms. '",
 "        method='One frozen baseline executable and installation identity, with a benchmark-only agent loading the compiled 128/256 reader or preserving the original reader. '")
(work/'skycache_channel_benchmark.py').write_text(s)
compile(s,'skycache_channel_benchmark.py','exec')
print('Prepared direct-endpoint channel comparison with separate warm/cold cohorts')
