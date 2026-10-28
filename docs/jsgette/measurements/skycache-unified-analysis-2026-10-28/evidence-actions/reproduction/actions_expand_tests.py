from pathlib import Path
import json,shutil,sys,hashlib,xml.etree.ElementTree as ET
from urllib.parse import urlsplit,unquote
root=Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-evidence')/sys.argv[1]
by_target={}
for line in (root/'build.bep.json').read_text().splitlines():
    event=json.loads(line)
    if 'testResult' not in event: continue
    result=event['testResult']; ident=event['id']['testResult']; label=ident['label']
    assert result['status']=='PASSED', (label,result['status'])
    data=by_target.setdefault(label,{'xml_cases':0,'skipped':0,'executed_cases':0,'shards':0})
    files=result.get('testActionOutput',[])
    for file in files:
        if file['name'] not in ('test.xml','test.log'): continue
        path=Path(unquote(urlsplit(file['uri']).path))
        if not path.exists():
            logs=Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-build/execroot/_main/bazel-out/k8-fastbuild/testlogs')/label.removeprefix('//').replace(':','/')
            matches=[p for p in logs.rglob(file['name']) if hashlib.sha256(p.read_bytes()).hexdigest()==path.parts[-2]]
            assert len(matches)==1,(file,matches)
            path=matches[0]
        target=root/'test-results'/label.split(':')[-1]/('shard-'+str(ident.get('shard',0)))
        target.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target/file['name'])
        if file['name']=='test.xml':
            xml=ET.parse(path); cases=xml.findall('.//testcase'); skipped=xml.findall('.//skipped')
            assert cases and not xml.findall('.//failure') and not xml.findall('.//error')
            data['shards']+=1; data['xml_cases']+=len(cases); data['skipped']+=len(skipped)
            data['executed_cases']+=len(cases)-len(skipped)
assert len(by_target)==4,by_target
out={'targets':by_target,'executed_cases':sum(x['executed_cases'] for x in by_target.values())}
(root/'expanded-tests.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
