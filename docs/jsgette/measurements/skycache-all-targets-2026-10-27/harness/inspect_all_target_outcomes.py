from skycache_all_common import *
import collections

d=json.loads((ROOT/'baseline-matrix-heap14-results.json').read_text())
rows=[]
for r in d['runs']:
    if r['preparation']:continue
    aborts=[]
    for line in (ROOT/(r['label']+'.bep.json')).read_text().splitlines():
        event=json.loads(line)
        if 'aborted' in event:aborts.append(dict(id=event.get('id'),**event['aborted']))
    skipped=sorted(e['id']['targetCompleted']['label'] for e in aborts if e['reason']=='SKIPPED')
    assert len(skipped)==122 and len(r['successful_targets'])==5536
    rows.append(dict(label=r['label'],target_patterns=d['workload']['targets'],
        expanded_selected_targets=len(set(skipped)|set(r['successful_targets'])),
        successful_target_events=len(r['successful_targets']),skipped_labels=skipped,
        aborted_events=aborts,aborted_reason_counts=dict(collections.Counter(e['reason'] for e in aborts))))
save(ROOT/'target-outcomes.json',rows)
assert all(r['skipped_labels']==rows[0]['skipped_labels'] for r in rows)
print(json.dumps(dict(selected=rows[0]['expanded_selected_targets'],completed=rows[0]['successful_target_events'],
    skipped_count=len(rows[0]['skipped_labels']),skipped_examples=rows[0]['skipped_labels'][:10],
    aborted_counts=rows[0]['aborted_reason_counts'],aborted_examples=rows[0]['aborted_events'][:3]),indent=2))
