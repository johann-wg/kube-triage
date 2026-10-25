| 指標 | 正解数 | 正解率 |
|---|---|---|
| カテゴリ | 15/15 | 100.0% |
| カテゴリ + 原因 | 15/15 | 100.0% |

| シナリオ | 正解 | 判定 | 結果 |
|---|---|---|---|
| crash-bad-command | CRASH_LOOP / bad_command | CRASH_LOOP / bad_command | OK |
| crash-dependency | CRASH_LOOP / dependency_unreachable | CRASH_LOOP / dependency_unreachable | OK |
| crash-missing-env | CRASH_LOOP / missing_env | CRASH_LOOP / missing_env | OK |
| crash-permission | CRASH_LOOP / permission | CRASH_LOOP / permission | OK |
| healthy | HEALTHY | HEALTHY | OK |
| image-not-found | IMAGE_PULL / image_not_found | IMAGE_PULL / image_not_found | OK |
| init-failure | CRASH_LOOP / dependency_unreachable | CRASH_LOOP / dependency_unreachable | OK |
| insufficient-memory | UNSCHEDULABLE / insufficient_resources | UNSCHEDULABLE / insufficient_resources | OK |
| liveness-probe | PROBE_FAILURE / liveness | PROBE_FAILURE / liveness | OK |
| missing-configmap | CONFIG_ERROR / missing_configmap | CONFIG_ERROR / missing_configmap | OK |
| missing-secret | CONFIG_ERROR / missing_secret | CONFIG_ERROR / missing_secret | OK |
| node-selector | UNSCHEDULABLE / node_selector | UNSCHEDULABLE / node_selector | OK |
| oom | OOM_KILLED | OOM_KILLED | OK |
| pvc-unbound | UNSCHEDULABLE / pvc_unbound | UNSCHEDULABLE / pvc_unbound | OK |
| readiness-probe | PROBE_FAILURE / readiness | PROBE_FAILURE / readiness | OK |
