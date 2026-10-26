---
status: "active"
document_type: "backlog"
record_id: "BL-20261026-001"
record_state: "open"
date: "2026-10-26"
updated: "2026-10-26"
priority: "low"
item_type: "enhancement"
source_idea: ""
review_after: ""
promoted_to: ""
result: ""
reason: ""
supersedes: ""
superseded_by: ""
---
# 只用电池时让卡片进 light sleep 省电

## Summary

第一版只用电池时只关背光、CPU 不睡，按约 40 mA 估算能撑 10 小时以上。light sleep（保持蓝牙连接、CPU 在空闲时浅睡的省电模式）能让续航更长，但 ESP-IDF v5.5 文档对蓝牙连着时能不能进 light sleep 说法互相矛盾，要在真卡片上验证。等第一版用满一周、知道实际续航够不够再决定做不做。依据：设计规格第 7 节。

## Origin

- Captured directly.

## Notes

- Add evidence, constraints, and decisions here as the item is reviewed.
