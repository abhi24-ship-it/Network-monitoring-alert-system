| scenario | runs | expected alerts detected | mean time to detect | false alarms |
|---|---|---|---|---|
| normal | 3 | - | - | - |
| abnormal | 3 | 6/6 | 5.6 s | CPU_SATURATION on 10.0.0.3 (x2) |
| benign_backup | 3 | - | - | CPU_SATURATION on 10.0.0.3 (x3); HOST_FLOOD on 10.0.0.3 (x3); LINK_CONGESTION on 10.0.0.4 (x3) |
| slow_attack | 3 | 0/3 | - | - |
| telemetry_kill | 3 | 3/3 | 7.2 s | - |

| | something wrong | nothing wrong |
|---|---|---|
| alert raised | TP = 9 | FP = 11 |
| no alert | FN = 3 | TN = 3 |
