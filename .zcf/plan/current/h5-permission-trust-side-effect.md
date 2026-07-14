# H5 Permission、Trust 与 Side-effect Gate

## Context

- 唯一规划依据：仓库上级 `plan.md` 的 H5。
- 范围：后端权限决策、trust 降级、pending approval、worker 继承、tool metadata 与 canon/memory/draft/plan 副作用入口。
- 非目标：RBAC、多租户授权、前端设计、新业务功能、Git 提交。

## Contract

1. 单一 owner 接收 operation、resource scope、payload fingerprint、trust context、parent restrictions 和 actor。
2. precedence 固定为 deny > ask > allow；未知 operation 为 ask。
3. 后台 actor 无法完成人工 approval 时，ask 收紧为 deny。
4. pending action 绑定完整 decision fingerprint，resource/payload/trust/actor 任一变化均不可消费。
5. worker 继承父级 deny、external/egress 和 trust 限制。
6. tool registry metadata 与 runtime decision 使用同一 owner。
7. canon、memory、draft、plan 的受治理写入在领域执行入口校验 grant。

## Steps

1. 新增 typed PermissionDecision、规范化 fingerprint 与 precedence 合并。
2. 将 trust permission compatibility API 委托给单一 owner。
3. 升级 pending action 为 decision-bound、single-use approval。
4. 迁移 worker 与 tool registry。
5. 为领域写入入口增加显式 execution grant，并迁移调用方。
6. 添加 precedence、tamper、replay、trust escalation、worker inheritance 和 gate contract tests。
7. 执行定向测试及完整后端门禁，只更新 `plan.md`。

## Rollback

变更保持旧 permission helper 和 pending action 参数兼容；若迁移失败，可逐调用点退回兼容入口，不涉及数据删除或 schema destructive migration。
