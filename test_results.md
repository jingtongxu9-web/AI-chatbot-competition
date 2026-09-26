# Checkpoint 1 开发集测试记录

- 测试日期：2026-09-26
- 测试方式：本地运行 `python main.py "<question>"`
- 索引构建：6,458 chunks，来源于 1,466 个页面
- Chroma 版本：`1.5.9`（与 `submission_repo/requirements.txt` 固定版本一致）
- Chat / Embedding deployment：`gpt-4o-mini` / `text-embedding-3-small`
- 文本切块参数：chunk size 800，overlap 100
- 记录来源：用户提供的终端截图
- 评分说明：此处仅按输出与 `dev_set.json` 参考答案进行人工初步比对；未运行官方评分器，不能视为正式分数。

| Level | 测试问题 | 机器人回答 | 参考答案 | 初步结果 |
|---|---|---|---|---|
| 1 | What is 3D printing also known as? | 3D printing technology. | Additive manufacturing | 不匹配 |
| 1 | What does FDM stand for in 3D printing? | The context does not contain the answer. My best guess is Fused Deposition Modeling. | Fused deposition modelling | 含义匹配（Modeling/Modelling 拼写差异）；回答同时声明没有上下文依据，建议后续核查检索证据 |
| 1 | Which faculty would you expect an engineering innovation centre to belong to? | Faculty of Engineering | The Faculty of Engineering | 匹配 |
| 2 | What is the capacity of Makerspace A? | 153 square meter | 40 | 不匹配 |
| 2 | When was the deadline for the first round of the Funding Scheme in 2025? | October 13, 2023 | 17 October 2025 | 不匹配 |
| 2 | Which two materials are banned from the laser cutter? | PVC and Teflon. | PVC and polycarbonate | 部分匹配（PVC 正确，Teflon 与参考答案不符） |

## 初步结论

- Lv1：1 题明确匹配，1 题含义匹配但模型称缺少上下文，1 题不匹配。
- Lv2：2 题不匹配，1 题部分匹配。
- 当前记录不是官方评分结果；Checkpoint 1 是否通过需以课程/主办方的评分器或判分规则为准。
- Makerspace A 容量问题曾出现不同回答，应优先检查该题的检索证据与索引内容。

## 后续补充

后续可在表格中增加“检索证据”“重测回答”“官方/人工评分”“备注”等列，并记录每次测试日期，保留结果变化轨迹。
