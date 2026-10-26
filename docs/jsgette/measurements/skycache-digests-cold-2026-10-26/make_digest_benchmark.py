from pathlib import Path
work=Path(__file__).parent
s=(work/'skycache_channel_benchmark.py').read_text()
def replace(old,new):
 global s
 assert old in s,old
 s=s.replace(old,new)
replace('import grpc','import grpc\nfrom cold_pages import evict')
replace('skycache-channels-2026-10-26','skycache-digests-2026-10-26')
replace("    parser.add_argument('--cold', action='store_true')", "    parser.add_argument('--cohort', choices=['warm','cold','eager','empty','diagnostic'],default='warm')")
replace('    args = parser.parse_args()', '    args = parser.parse_args()\n    cohort = args.cohort')
replace("executables={'baseline':binary,'channels128':candidate128,'channels256':candidate}","executables={'channels':candidate128,'scratch':candidate}")
replace("hashes['channels128']","hashes['channels']")
replace('for i in range(16)','for i in range(32)')
replace("copy_function=os.link)\n        shutil.copytree(seed_preparation/'catalog-seed'", "copy_function=shutil.copy2)\n        shutil.copytree(seed_preparation/'catalog-seed'")
replace("implementation='baseline'", "implementation='channels'")
replace("        arm, jfr = variant, diagnostic", "        arm, jfr = variant, diagnostic\n        local_mode=arm in ['local','local-empty','eager']\n        remote_variant='remote-cold' if arm=='eager' else arm")
replace("        startup=startup_base+['--host_jvm_args=-javaagent:'+str(ROOT.parent/'agent/agent.jar')+'='+implementation]",
 "        startup=startup_base+['--host_jvm_args=-javaagent:'+str(ROOT.parent/'agent/agent.jar')+'='+implementation]\n        if diagnostic:\n            startup+=['--host_jvm_args=-javaagent:'+str(ROOT.parent/'agent/cold-probe.jar')+'='+str(ROOT/(label+'-stages.jsonl'))]")
replace("cache/'leased-v2'/part, copy_function=os.link)","cache/'leased-v2'/part, copy_function=shutil.copy2)")
replace("        else:\n            if arm=='remote-warm':", "        elif arm!='local-empty':\n            if arm=='remote-warm':")
replace("+variant)","+remote_variant)")
replace("        simple(startup+['clean'", "        startup_started=time.monotonic()\n        simple(startup+['clean'")
replace("        simple(startup+['dump'", "        row['startup_and_clean_seconds']=time.monotonic()-startup_started\n        simple(startup+['dump'")
replace("                if arm=='local':", "                if local_mode:")
replace("        t=time.monotonic()\n        row['command_start_monotonic']=t", """        if cohort in ['cold','eager','diagnostic']:
            disk_root=cache/'leased-v2' if arm=='local' else ROOT/('metadata-cas-'+remote_variant)
            row['page_cache']=evict(disk_root)
            assert row['page_cache']['resident_fraction_after'] < 0.001, row['page_cache']
        t=time.monotonic()
        row['command_start_monotonic']=t
        if arm=='eager':
            hydration=[str(JDK/'bin/java'),'-Xmx4g','-javaagent:'+str(ROOT.parent/'agent/agent.jar')+'=scratch',
                '-cp',str(seed_preparation/'helper-classes')+':'+str(seed_preparation/'server.jar'),
                'com.google.devtools.build.lib.skyframe.serialization.local.HydrateSkycacheSeed',
                str(cache),preparation['namespace'],'grpc://127.0.0.1:'+str(port)]
            simple(hydration,label+'-hydrate',600)
            row['hydrate_seconds']=time.monotonic()-t
            row['hydrate_argv']=hydration
            hydrate_log=(ROOT/(label+'-hydrate.log')).read_text()
            row['hydrate_stats']=json.loads(next(x for x in hydrate_log.splitlines() if x.startswith('{')))
        build_started=time.monotonic()""")
replace("        row['seconds']=time.monotonic()-t", "        row['seconds']=time.monotonic()-t\n        row['build_seconds']=time.monotonic()-build_started")
replace("        if arm!='local':\n            row['rpc']", "        if not local_mode or arm=='eager':\n            row['rpc']")
replace("        if arm!='local' and implementation!='baseline':", "        if not local_mode:")
replace("int(implementation.removeprefix('channels'))", "128")
replace("        assert row['local-skycache']['writes']>0 and row['local-skycache']['reused_witness_blocks']==103,label", "        assert row['local-skycache']['writes']>0,label\n        if arm!='local-empty':\n            assert row['local-skycache']['reused_witness_blocks']==103,label")
replace("        if arm=='local':\n            assert row['local-skycache']['published'],label", "        if local_mode:\n            assert row['local-skycache']['published'],label")
replace("        if stabilization:\n            assert row['analysis_hits']", "        if arm=='local-empty':\n            assert (row['analysis_hits'],row['execution_hits'])==(0,0),label\n        elif stabilization:\n            assert row['analysis_hits']")
replace("        if arm!='local':\n            assert row['committed'],label", "        if not local_mode:\n            assert row['committed'],label")
replace("            assert all(x['status']=='OK' for x in row['rpc'])", "            if arm=='remote-cold':\n                assert row['remote-skycache']['downloaded_bytes']==1463742717,label\n            assert all(x['status']=='OK' for x in row['rpc'])")
a=s.index("        run('warmup-local'");b=s.index("        results['status']='passed'",a)
s=s[:a]+'''        arms={
            'warm':[('remote-warm','channels'),('remote-warm','scratch')],
            'cold':[('local','channels'),('remote-cold','channels'),('remote-cold','scratch')],
            'eager':[('eager','scratch')],
            'empty':[('local-empty','channels')],
            'diagnostic':[('local','scratch'),('remote-cold','scratch')],
        }[args.cohort]
        index=0
        for variant,implementation in arms:
            run('warmup-'+variant+'-'+implementation,variant,index,warmup=True,implementation=implementation)
            index+=1
        orders={
            'warm':[0,1,1,0,0,1],
            'cold':[0,1,2,2,1,0,1,0,2],
            'eager':[0,0,0],
            'empty':[0,0,0],
            'diagnostic':[0,1],
        }[args.cohort]
        for number,which in enumerate(orders,1):
            variant,implementation=arms[which]
            run('r'+str(number)+'-'+variant+'-'+implementation,variant,index,
                diagnostic=args.cohort=='diagnostic',implementation=implementation)
            index+=1
''' + s[b:]
replace("One frozen baseline executable and installation identity, with a benchmark-only agent loading the compiled 128/256 reader or preserving the original reader.",
 "One frozen baseline installation with startup-only replacement of the tested 128-channel reader or its digest/buffer optimization. Cold disk arms evict private metadata pages with fadvise and verify residency using mincore. Eager includes production hydration before local restore; empty starts without a publication.")
replace("        local_mode=arm in ['local','local-empty','eager']", "        local_mode=arm in ['local','local-empty','eager','eager-parallel']")
replace("if arm=='eager' else arm", "if arm.startswith('eager') else arm")
replace("        if arm=='eager':", "        if arm.startswith('eager'):")
replace("if not local_mode or arm=='eager':", "if not local_mode or arm.startswith('eager'):")
replace("'-cp',str(seed_preparation/'helper-classes')+':'+str(seed_preparation/'server.jar'),", "'-cp',str(ROOT/'helper-classes')+':'+str(seed_preparation/'helper-classes')+':'+str(seed_preparation/'server.jar'),")
replace("'com.google.devtools.build.lib.skyframe.serialization.local.HydrateSkycacheSeed',", "'com.google.devtools.build.lib.skyframe.serialization.local.HydrateSkycacheSeed'+('Parallel' if arm=='eager-parallel' else ''),")
replace("'eager':[('eager','scratch')]", "'eager':[('eager','scratch'),('eager-parallel','scratch')]")
replace("'eager':[0,0,0]", "'eager':[0,1,1,0,0,1]")
replace("    worker = proxy_server = proxy = None", """    if cohort=='eager':
        subprocess.run([str(JDK/'bin/javac'),'-proc:none','-cp',str(seed_preparation/'server.jar'),
            '-d',str(ROOT/'helper-classes'),str(Path(__file__).parent/'HydrateSkycacheSeedParallel.java')],check=True)
    worker = proxy_server = proxy = None""")
replace("ROOT.parent/'agent/cold-probe.jar'", "ROOT.parent/'agent/cold-probe-v2.jar'")
replace("    if cohort=='eager':", "    if cohort=='diagnostic':\n        subprocess.run([os.sys.executable,str(Path(__file__).parent/'prepare_cold_probe.py')],check=True)\n    if cohort=='eager':")
replace("'diagnostic':[('local','scratch'),('remote-cold','scratch')]", "'diagnostic':[('local','scratch'),('remote-cold','scratch'),('remote-warm','channels'),('remote-warm','scratch')]")
replace("'diagnostic':[0,1]", "'diagnostic':[0,1,2,3]")
replace("if cohort in ['cold','eager','diagnostic']:", "if cohort in ['cold','eager','diagnostic'] and arm!='remote-warm':")
replace("choices=['warm','cold','eager','empty','diagnostic']", "choices=['warm','cold','eager','empty','diagnostic','confirm']")
replace("            'warm':[('remote-warm','channels'),('remote-warm','scratch')],", "            'warm':[('remote-warm','channels'),('remote-warm','scratch')],\n            'confirm':[('local','channels'),('remote-warm','channels'),('remote-warm','scratch')],")
replace("            'warm':[0,1,1,0,0,1],", "            'warm':[0,1,1,0,0,1],\n            'confirm':[0,1,2,2,1,0,1,0,2],")
replace("if cohort=='eager':", "if cohort in ['eager','confirm']:")
replace("if arm=='eager-parallel' else ''", "if arm.startswith('eager-parallel') else ''")
replace("if cohort in ['cold','eager','diagnostic'] and arm!='remote-warm':", "if (cohort in ['cold','eager','diagnostic'] and arm!='remote-warm') or arm.startswith('eager'):")
replace("'confirm':[('local','channels'),('remote-warm','channels'),('remote-warm','scratch')]", "'confirm':[('local','channels'),('remote-warm','channels'),('remote-warm','scratch'),('eager-parallel-remote','scratch')]")
replace("'confirm':[0,1,2,2,1,0,1,0,2]", "'confirm':[0,1,2,3,3,2,1,0,2,3,0,1]")
(work/'skycache_digest_benchmark.py').write_text(s)
compile(s,'skycache_digest_benchmark.py','exec')
print('Prepared matched digest/cold/eager comparison')
