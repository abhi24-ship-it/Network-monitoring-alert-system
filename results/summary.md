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

Thresholds in these runs: util_high = 60% (the earlier 70% default missed HOST_FLOOD in the VM, where h3’s measured uplink was 61–76% against 9 Mbps offered). 
Five of the 11 false alarms are CPU_SATURATION warnings that appear in the last seconds, after h3’s traffic ends while its staged CPU value is still reported. LINK_CONGESTION fired within 2.7–3.4 s of the attack start, and HOST_FLOOD within 4.7–14.7 s.
3 runs per scenario on one VM
