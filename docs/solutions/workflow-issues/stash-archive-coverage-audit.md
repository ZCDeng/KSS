---
module: git-workflow
date: 2026-07-23
problem_type: workflow_issue
component: development_workflow
severity: medium
resolution_type: workflow_improvement
applies_when:
  - "stash 列表里有存活超过一周的旧 stash，准备清理"
  - "stash 里混有运行态数据文件（cs_data_*.csv、storage/ 等），存在被误 restore 的风险"
  - "需要确认某个旧分支/stash 的改动是否已被 main 覆盖，能否安全废弃"
tags:
  - git-stash
  - archive-branch
  - coverage-audit
  - data-safety
  - untracked-files
---

# 旧 stash 清理：archive 分支保引用 + 四态覆盖比对

## Context

2026-07-22 发生过一次真实事故：并行 Claude 会话执行 `git restore --source="stash@{0}"`，把已更新到 7-21 的 cs_data 行情数据覆盖回 7-9，App 自选价格停在旧日期。根因不是那次操作本身，而是**旧 stash 的存在**——只要 `git stash list` 里还有条目，就永远存在"顺手 restore"的入口。

2026-07-23 清理时，4 个 stash（最老的存活约两周）共压着 200+ 个改动文件和 40 万行 untracked 内容。直接 drop 有丢失风险，直接保留则数据雷继续存在。

## Guidance

分两步：先归档保引用，再做覆盖比对确认没有独有内容。

**第一步：archive 分支化解，然后清空 stash 列表。**

```bash
# 每个 stash 建一个本地分支保住 commit 引用（stash 本质是 commit，分支指上去就不会被 GC）
git branch stash-archive/<语义名> stash@{N}
# drop 会使编号前移，必须从大编号往小删
git stash drop stash@{3} && git stash drop stash@{2} && git stash drop stash@{1} && git stash drop stash@{0}
```

archive 分支纯本地、不推远端。快照随时可通过 `git stash apply stash-archive/<名>` 或 checkout 找回，但 stash 列表清空后，"顺手 restore"的入口就不存在了。

**第二步：四态覆盖比对，判定 archive 分支是否还有独有内容。**

stash commit 有 2-3 个父提交：`^1` 是当时的基点，`^3`（如果有）是 untracked 文件快照。对基点到 stash 的每个改动文件，与 main 比 blob 哈希，分四态：

| 判定 | 含义 | 处理 |
|------|------|------|
| IDENTICAL | 与 main 内容完全一致 | 已合入，无需动作 |
| SUPERSEDED | main 自基点后改过此文件 | 被更新实现取代，抽查即可 |
| STASH_ONLY | main 自基点后没动过此文件 | **stash 独有改动，逐个人工判断** |
| ABSENT | untracked 文件 main 里不存在 | **逐个判断是垃圾还是丢失的工作** |

核心判定脚本（tracked 部分）：

```bash
b=stash-archive/<名>; base=$(git rev-parse "$b^1")
git diff --name-only "$base" "$b" | while read -r f; do
  sb=$(git rev-parse -q --verify "$b:$f"); mb=$(git rev-parse -q --verify "main:$f")
  if [ -z "$mb" ]; then echo "GONE_ON_MAIN $f"
  elif [ "$sb" = "$mb" ]; then echo "IDENTICAL    $f"
  elif git diff --quiet "$base" main -- "$f"; then echo "STASH_ONLY   $f"
  else echo "SUPERSEDED   $f"; fi
done
```

untracked 部分（`^3` 存在时）：`git ls-tree -r --name-only "$b^3"` 列出文件，逐个查 `main:$f` 是否存在。

## Why This Matters

本次核查的实际结果说明了每一态的价值：

- 3 个分支 200+ 文件里，98% 是 IDENTICAL 或 SUPERSEDED——不做比对就只能凭感觉赌"应该都合了"。
- 7 个 STASH_ONLY 文件拼出了一个被遗忘的完整 WIP：cron 退出码标准化（引入 cron_exit_codes.py 三态退出码并迁移 7 个 cron 脚本）。该特性从未落地，后来被 pipeline_markers 完成标记 + selfcheck 看门狗架构取代——比对给了"确认放弃"的依据，而不是默默蒸发。
- 两份 plan 文档（memory-recall-ranking、perilla-enrichment）只存在于 stash 的 untracked 快照里，main 和工作区都没有。docs/plans 是入库惯例，这两份当时 untracked 被 stash 卷走后再没回来，其中一份还是"已规划未开工"、开工时必须用的文档。靠 ABSENT 核查捞回（commit a90a0a42）。

**untracked 文件被 stash 卷走是最隐蔽的丢失路径**：`git stash -u` 之后文件从工作区消失，git status 干净，没有任何提示它曾存在过。plan/文档类文件写完常处于 untracked 状态，最容易中招。

## When to Apply

- 清理任何存活超过一周的 stash 之前——一周以上意味着 main 大概率已分叉，直接 pop 会冲突或覆盖。
- stash 含运行态数据文件时**必须**走此流程，且恢复类操作前先 `git stash show --name-only` 检查（血的教训见 Context）。
- 反向预防：写完 plan/文档立即 `git add`，别让它以 untracked 状态过夜。

## Examples

本次清理的完整案例：3 个 stash 归档为 `stash-archive/wip-0709-bridge-plists`、`stash-archive/intelview-0709`、`stash-archive/pre-packaging-0708`，第 4 个（仅 .gitignore 规则）直接 drop。四态比对后唯二捞回物是两份 plan 文档；cs_data 类 CSV 判定为无需保留——运行态数据的真相源是 Tushare 增量链，不是 git。
