| 指標 | 正解数 | 正解率 |
|---|---|---|
| カテゴリ | 17/18 | 94.4% |
| カテゴリ + 原因 | 16/18 | 88.9% |

| シナリオ | 正解 | 判定 | 結果 |
|---|---|---|---|
| 01_healthy | HEALTHY | HEALTHY | OK |
| 02_oom_killed | OOM_KILLED | OOM_KILLED | OK |
| 03_image_not_found | IMAGE_PULL / image_not_found | IMAGE_PULL / image_not_found | OK |
| 04_image_registry_auth | IMAGE_PULL / registry_auth | IMAGE_PULL / registry_auth | OK |
| 05_missing_configmap | CONFIG_ERROR / missing_configmap | CONFIG_ERROR / missing_configmap | OK |
| 06_missing_secret | CONFIG_ERROR / missing_secret | CONFIG_ERROR / missing_secret | OK |
| 07_insufficient_memory | UNSCHEDULABLE / insufficient_resources | UNSCHEDULABLE / insufficient_resources | OK |
| 08_pvc_unbound | UNSCHEDULABLE / pvc_unbound | UNSCHEDULABLE / pvc_unbound | OK |
| 09_taint | UNSCHEDULABLE / taint | UNSCHEDULABLE / taint | OK |
| 10_node_selector | UNSCHEDULABLE / node_selector | UNSCHEDULABLE / node_selector | OK |
| 11_liveness_probe | PROBE_FAILURE / liveness | PROBE_FAILURE / liveness | OK |
| 12_readiness_probe | PROBE_FAILURE / readiness | PROBE_FAILURE / readiness | OK |
| 13_crash_missing_env | CRASH_LOOP / missing_env | CRASH_LOOP / missing_env | OK |
| 14_crash_dependency | CRASH_LOOP / dependency_unreachable | CRASH_LOOP / dependency_unreachable | OK |
| 15_crash_bad_command | CRASH_LOOP / bad_command | CRASH_LOOP / bad_command | OK |
| 16_crash_permission | CRASH_LOOP / permission | CRASH_LOOP / permission | OK |
| 17_crash_invalid_config | CRASH_LOOP / invalid_config | CRASH_LOOP | 原因NG |
| 18_init_container_failure | CRASH_LOOP / dependency_unreachable | UNKNOWN | NG |
