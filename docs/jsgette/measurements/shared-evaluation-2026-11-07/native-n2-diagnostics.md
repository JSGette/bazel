# Native N2 diagnostics

Single trials; no performance or milestone verdict. Reference is correctness-only.

| Role | Verified | Build wall s | Raw scope CPU s | Python CPU bounds s | Non-harness CPU bounds s | Daemon CPU s | PSS MiB | Complete samples |
| --- | --- | ---: | ---: | --- | --- | ---: | ---: | --- |
| ref | True | 16.513 | 48.657 | 0.240–0.241 | 48.416–48.417 | 48.220 | 1180.838 | 34/36 |
| private | True | 18.465 | 49.704 | 0.250–0.251 | 49.453–49.454 | 49.260 | 1194.435 | 37/39 |
| native | True | 17.722 | 40.045 | 0.193–0.193 | 39.851–39.852 | 39.660 | 1062.323 | 36/38 |

## Preparation and lifecycle

| Role | Preparation command walls s | Lifecycle scope CPU s | All-client BEP overlap s |
| --- | --- | ---: | ---: |
| ref | 109.462, 35.794 | 216.968 | 15.753 |
| private | 110.391, 32.061 | 214.074 | 16.660 |
| native | 108.744, 30.876 | 205.806 | 17.099 |

Preparation CPU is not separately measured. Lifecycle CPU includes fixture/seed work, preparation, measured work, verification and shutdown.
Non-harness bounds include clients/daemons/compiler descendants and do not remove observer perturbation. Daemon endpoints extend beyond the frozen build scope window.
PSS is a sampled unique-process peak including boundary reads; cgroup peaks include preparation. Full pressure, output/UUID proof and input hashes are in the JSON.

Comparison: `{"blocked_reason": null, "build_clients_wall_seconds_native_over_private": {"bounds": [0.9597323213869697, 0.9597323213869697], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}, "daemon_cpu_seconds_native_over_private": {"bounds": [0.8051157125456759, 0.8051157125456759], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}, "diagnostic_only": true, "milestone_verdict": null, "non_harness_cpu_native_over_private": {"bounds": [0.8058281952612345, 0.8058523365680008], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}, "raw_scope_cpu_seconds_native_over_private": {"bounds": [0.8056674667023234, 0.8056674667023234], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}, "reported_peak_daemon_pss_bytes_native_over_private": {"bounds": [0.8893942528049605, 0.8893942528049605], "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict", "status": "known"}}`
