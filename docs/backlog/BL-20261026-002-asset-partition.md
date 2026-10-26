---
status: "active"
document_type: "backlog"
record_id: "BL-20261026-002"
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
# 角色素材单独放一个分区

## Summary

现在字库和角色图跟程序打在同一个 4 MB 程序分区里，预编译固件约 1.8 MB，每次重新生成都进 git 历史。把素材放进单独的分区后，只改程序时不用重刷素材，预编译固件也会小很多。第一版素材和程序一起改，一次刷完更简单，所以先不做。依据：设计规格第 8 节讲分区表的那段。

## Origin

- Captured directly.

## Notes

- Add evidence, constraints, and decisions here as the item is reviewed.
