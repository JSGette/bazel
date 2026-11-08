# Native N10 diagnostics

Single trials; no performance or milestone verdict. Reference is correctness-only.

| Role | Verified | Build wall s | Raw scope CPU s | Python CPU bounds s | Non-harness CPU bounds s | Daemon CPU s | PSS MiB | Complete samples |
| --- | --- | ---: | ---: | --- | --- | ---: | ---: | --- |
| ref | True | 122.826 | 283.888 | 10.123–10.123 | 273.764–273.765 | 271.940 | 5388.759 | 243/245 |
| private | True | 131.735 | 285.165 | 11.323–11.324 | 273.841–273.841 | 271.830 | 5606.068 | 264/266 |
| native | True | 111.598 | 98.229 | 5.904–5.905 | 92.324–92.325 | 90.840 | 4220.933 | 224/226 |

## Preparation and lifecycle

| Role | Preparation command walls s | Lifecycle scope CPU s | All-client BEP overlap s |
| --- | --- | ---: | ---: |
| ref | 114.891, 42.139, 38.760, 39.632, 41.745, 39.312, 35.758, 45.639, 41.744, 35.169 | 939.083 | 26.246 |
| private | 107.940, 32.809, 31.478, 40.794, 33.868, 34.216, 33.323, 46.216, 32.617, 39.628 | 923.760 | 57.708 |
| native | 110.679, 29.468, 38.230, 35.225, 38.629, 35.227, 38.681, 41.670, 38.267, 34.405 | 737.867 | 23.656 |

Preparation CPU is not separately measured. Lifecycle CPU includes fixture/seed work, preparation, measured work, verification and shutdown.
Non-harness bounds include clients/daemons/compiler descendants and do not remove observer perturbation. Daemon endpoints extend beyond the frozen build scope window.
PSS is a sampled unique-process peak including boundary reads; cgroup peaks include preparation. Full pressure, output/UUID proof and input hashes are in the JSON.

Comparison: `{"blocked_reason": null, "build_clients_wall_seconds_native_over_private": {"bounds": [0.8471419875679557, 0.8471419875679557], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}, "daemon_cpu_seconds_native_over_private": {"bounds": [0.33417945039178903, 0.33417945039178903], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}, "diagnostic_only": true, "milestone_verdict": null, "non_harness_cpu_native_over_private": {"bounds": [0.33714385896900334, 0.33714693840166365], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}, "raw_scope_cpu_seconds_native_over_private": {"bounds": [0.34446322284176156, 0.34446322284176156], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}, "reported_peak_daemon_pss_bytes_native_over_private": {"bounds": [0.7529220741892766, 0.7529220741892766], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}}`
