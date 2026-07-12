# Appendix：storage/ 存储盘点

> plan [2026-07-12-005-feat-release-hardening-settings-plan.md](2026-07-12-005-feat-release-hardening-settings-plan.md) / U13（R13）产物。作为 U14-U17 存储统一割接的真相源——迁移范围、目标表名、主键均以本文档为准，不在割接单元里现场重新调研。

## 分层原则（照抄 plan Key Decisions，供本文档自洽）

- **Tier A（进统一 `kss.db` SQLite）**：JSON/CSV/YAML 形态的注册表、衍生缓存、追加型台账。
- **Tier B（保留 parquet/CSV 原格式，DuckDB 就地查询）**：大宗 OHLCV/宏观行情数据。
- **Tier C（保留文件）**：markdown 复盘/报告文档、日志、既有分时隔离库 `intraday_quotes.db`（自带表契约与脱敏纪律，不并入）。
- **第四类（本文档新增，plan 未预留）——死重/孤儿，排除出割接范围**：调研中发现若干目录/文件在当前分支已无任何写入方或读取方（不是"低频"，是"零引用"）。这类不属于 Tier A/B/C 中任何一层的迁移目标，而是独立的清理决策，留给作者，本次割接不动它们（既不迁移也不主动删除）。

## 全量清单（36 项，逐条对齐 `storage/` 实际顶层目录/文件）

### Tier A — 迁入统一 `kss.db`

| 路径 | 格式/量级 | 写入方 | 读取方 | 更新节奏 | 目标表 | 主键 |
|---|---|---|---|---|---|---|
| `paper_trade/` | 19×json，76K | `scripts/paper_trade_log_mv.py:save_log_entry` (174-217)；ledger 侧写 `kss/prediction/ledger.py:record_prediction` (220) | bridge `paper-summary` (`kss_app_bridge.py:4467`)、MCP `get_paper_summary`；`scripts/weekly_summary.py`、`scripts/compute_pipeline_alpha.py:52` | daily（cron `paper_trade_daily` 09:05 工作日） | `paper_trade_picks` | `(prediction_date, symbol)` |
| `sector_rotation/*.json` | 36×json，13M（另 8×`.log` 为手工 probe 产物，见下方注记） | `kss/sector/hotspot_rotation.py:save_snapshot` (569-579)，触发自 `scripts/refresh_hotspot_rotation.py` | bridge `sector-rotation`/`sector-rotation-history`（`kss_app_bridge.py:4471-4472`）、MCP 同名两个工具；`scripts/compute_pipeline_alpha.py:54` | daily（cron `hotspot_rotation_daily` 17:50 工作日） | `sector_rotation_snapshots`（JSON blob 列，结构太嵌套不值得拆表） | `trade_date` |
| `mi_signals/` | 4×json，304K（含 `latest/` 镜像） | `kss/strategies/mi_pack.py:save_pack`（触发自 `scripts/run_mi_signal_pack.py`；cron `mi_signal_pack` 17:15 工作日） | 并入 `stock` 命令 / MCP `get_stock` 的 overlay（`kss_app_bridge.py:3066-3068`） | daily | `mi_signal_packs` | `(asof, symbol)` |
| `indicator_signals/` | 4×json，192K | `kss/indicators/pack.py:run_entry_pack` (304)，触发自 cron `indicator_signal_pack` 17:16 | `scripts/daily_review.py`、`kss/indicators/report.py`、`kss/indicators/ledger_bridge.py` | daily | `indicator_signal_packs` | `(entry_id, asof, symbol)` |
| `intel_radar/` | 1×json，232K（单文件覆写，非逐日） | `kss/news/radar.py:fetch_radar` (105-151)，触发自 cron `intel_radar_refresh` 09:00 工作日 + on-demand force | bridge `intel-radar`（`kss_app_bridge.py:4498`） | daily + on-demand | `intel_radar_cache`（单行，`generated_at` 覆写） | 无（singleton） |
| `intel_rewrites/` | 150×json，1.9M | `kss/storage/rewrite_pool.py:write_draft` (90-96)，触发自 bridge `intel-rewrite`/`intel-rewrite-run`（写命令） | `kss/news/rewrite.py:pool_ready_count` (397)；同两个 bridge 命令读回状态 | on-demand（无 cron） | `intel_rewrite_items` | `item_id` |
| `perilla_cache/` | 36×csv，716K | `kss/perilla_enrich/aggregate.py:_cached_df` (155-190)，触发自 cron `perilla_enrich_daily` 18:10 工作日 + on-demand | bridge `perilla-enrichment`（`kss_app_bridge.py:4476`）、MCP `get_perilla_enrichment` | daily 预热 + on-demand | `perilla_enrich_cache` | `(ts_code, kind)` |
| `prediction_ledger/ledger.db` | 已是 SQLite（110 行，128K） | `kss/prediction/ledger.py:record_prediction`(220)/`settle`(299)/`mark_data_missing`(397) | `_ledger_tracking`（`kss_app_bridge.py:1672-1710`，经 `paper-summary`/`get_paper_summary` 暴露）；`scripts/compute_pipeline_alpha.py:111` | daily（F1 写 + F2 结算，cron `ledger_settle` 15:35 工作日） | `predictions`（**原样并表**，schema 见 `kss/prediction/ledger.py:161-181`，不重新设计） | `prediction_id` |
| `factor_health/factor_health.db` | 已是 SQLite（`crashes`/`factor_lifecycle`/`ic_snapshots`，44K） | `kss/backtest/factor_health.py:record_ic_snapshot`(415)/`log_crash`(607)/`set_state`(683)，触发自 cron `factor_health` 15:50 工作日 + `indicator_signal_pack` 17:16 附带更新 | `scripts/compute_pipeline_alpha.py:44-49`（唯一外部读取方，纯内部 Python 调用） | daily | `crashes` / `factor_lifecycle` / `ic_snapshots`（**原样并表**） | 各表既有 PK（`factor_id, window_end, source` 等） |
| `indicator_registry.yaml` | 单文件，4K | `kss/indicators/registry.py:save_registry/upsert_entry/retire_entry`，触发自 bridge `indicator-solidify`/`indicator-retire`（人工确认写命令） | `scripts/run_indicator_signal_pack.py:26`、`scripts/daily_review.py:825`、bridge `indicator-lab-list` | event-driven 写 + 每交易日 cron 读 | `indicator_registry` | `entry_id` |
| `indicator_lab/` | 4×json，16K | `_persist_verdict`（`kss_app_bridge.py:4093-4106`），触发自 bridge `indicator-backtest` | `_indicator_lab_recent_verdicts`（`:4082-4092`），`indicator-suggest` 用于避免重复 NO-GO 参数 | on-demand（用户在指标实验室跑回测时） | `indicator_lab_verdicts` | `verdict_id`（新增自增列，原文件名无天然唯一键） |
| `pipeline_weights.json` | 单文件，4K | 无程序化写入——人工改（`compute_pipeline_alpha.py:372` 提示"权重更新需人工确认后手改"） | `_load_discovery_weights`（`kss_app_bridge.py:2771`），backs `get_discovery_candidates` | 静态配置，每次 discovery 调用读 | `pipeline_weights` | `weight_key` |
| `sector_review_config.json` | 单文件，4K | 无程序化写入——人工改 | `kss/sector/hotspot_rotation.py:398`，触发自 cron `hotspot_rotation_daily` 17:50 | 静态配置，每次板块复盘 cron 读 | `sector_review_config` | `config_key` |
| `themes_15th_5y.yaml` | 单文件，8K | 无程序化写入——**用户热改**（`kss/sector/themes.py:3` 注释原话） | `kss/sector/themes.py`、`kss/news/theme_match.py:171`、`kss/sector/commentary.py:738`；bridge `theme-leaders`（`:3931-3956`）、MCP `get_theme_leaders` | 静态配置，每次复盘/资讯/主题龙头调用读 | `theme_registry` | `theme_id` |
| `mi_rules.yaml` | 单文件，4K | 无程序化写入——人工改 | `kss/strategies/mi_pack.py:load_rules`(57-64)，`kss/indicators/registry.py:77`（`MI_ENTRY.rules_path`） | 静态配置，每次 `mi_signal_pack`/`formal_daily_review` cron 读 | `mi_rules` | `rule_key` |
| `stock_names.csv` | 单文件，24K | 无写入方——纯人工维护参考表 | `kss/sector/kcb_overlay.py:27`、`kss/macro/rotation.py:158-164`（industry_map 兜底）、`scripts/paper_trade_log_mv.py:426`、bridge `NAMES_PATH`（`:45,166-167`） | 静态，几乎每次涉及展示名的调用都读 | `stock_names` | `ts_code` |
| `app_runs/*.jsonl` | 1×jsonl，132K，追加写 | `_append_task_history`（`kss_app_bridge.py:678-681`），每次 bridge 任务运行后追加 | `_task_history`（`:684-697`），暴露为 orientation payload 的 `recentTaskRuns` | on-demand（每次用户在 app 内跑任务） | `app_task_runs` | `run_id`（新增自增列） |
| `watchlist_symbols.txt` | 单文件，4K，纯文本 | **Swift**：`ContentView.swift:syncWatchlistFile` (21-28)，真源是 `@AppStorage("watchlistSymbols")`（UserDefaults），`.txt` 只是给 Python 读的同步产物 | `scripts/collect_intraday.py:_load_watchlist_symbols`(233-238)（cron `collect_intraday` 15:05 daily）、`kss_app_bridge.py:_indicator_watchlist_symbols`(4040-4042)、`scripts/backtest_mi_watchlist.py`、`scripts/run_mi_signal_pack.py` | 写：UI 编辑触发；读：多个每日 cron | `watchlist` | `ts_code` |
| `intraday_session_cache/` | 11×json，344K，持久（非会话级临时） | `_save_intraday_session_cache`（`kss_app_bridge.py:4863-4884`），每次分时拉取成功后附带落盘 | `_load_local_session_bars`（`:4921-4939`），backs MCP `get_intraday_snapshot`，用作非交易时段本地降级 | on-demand（每次成功的实时拉取） | `intraday_session_cache` | `(symbol, session_date)` |

**待新建索引（Tier C 文件本身不迁移，只加一张定位索引表，供查询替代现有的目录 glob）：**

| 域 | 文件仍留 Tier C | 新增索引表 | 主键 | 备注 |
|---|---|---|---|---|
| `daily_review/*.md` | 是 | `daily_review_index` | `(review_date, ts_code)` | 现状零索引，5 处调用点各自 glob `*.md` 用文件名正则解析日期/代码（`_REVIEW_PERSYMBOL_RE`，`kss_app_bridge.py:341`）；索引表存路径+摘要字段 |
| `reports/` | 是 | `reports_index` | `report_id`（新增自增列） | 现状唯一的"索引"是 `_backtest_reports()`（`kss_app_bridge.py:426-435`）里硬编码的 8 个文件名列表——本次割接的主要动机之一，退休这个硬编码列表 |
| `etf_radar/*.json` + `*.commentary.md` | json 部分迁 Tier A（见下），md 留 Tier C | 并入 `etf_radar_snapshots`（json 内容）+ `etf_radar_commentary_index`（md 路径索引） | `trade_date` | 现状 3 处独立 glob（`_sector_reviews`/`_load_radar_archives`/`validate_predictions.py`） |
| `news_digest/*.json` + `*.md` | json 部分迁 Tier A（见下），md 留 Tier C | 并入 `news_digest_entries`（json 内容）+ 路径列指回 md | `(digest_date, scene)` | 现状 1 处 glob（`_news_digest`） |
| `trends/*.json` | 否——`trends` 本身内容体量小、结构规整，整个迁 Tier A，不留文件 | `trends_days` | `trade_date` | 单日文件名即键，不需要额外索引层，直接就是数据表 |
| `notes/*.md` + `*.json` | md 留 Tier C，json 部分迁 Tier A | `intel_digest_notes` | `(digest_date, track_key)` | **零读取方**（`kss/storage/notes.py:3-4` 原话"沉淀库只写不读"）——结构上够格进 Tier A，但优先级应垫底，因为没有任何东西在读它，割接价值存疑，留给 U14 决定是否一起搬还是暂缓 |

对应地，以下三个混合域的 json 部分也算进上面 Tier A 迁移范围（表已在上面独立列出，这里只是防止读者以为它们被漏了）：`etf_radar_snapshots`（PK `trade_date`）、`etf_radar_morning_alert_state`（`scripts/morning_divergence_alert.py:mark_alerted`，PK 无/singleton）、`news_digest_entries`（PK `(digest_date, scene)`）。

### Tier B — 保留原格式，DuckDB 就地查询

| 路径 | 格式/量级 | 写入方 | 读取方 | 更新节奏 |
|---|---|---|---|---|
| `macro/` | 32 文件（11×parquet 已入 `data_catalog.json` + 17×csv 按日存档 bond_china_yield + 3×json 小型索引 + 1×`000300_SH_*.parquet` 独立指数历史），108M | `scripts/update_macro_daily.py`（cron `macro_daily` 08:35 工作日）+ 若干同族脚本（`refresh_daily_basic.py`/`build_name_index.py`/`refresh_market_strip.py`），路径常量集中在 `kss/config/paths.py` | `kss/macro/{rotation,regime,derived,queries}.py`，consumed by `kss/sector/commentary.py`、`kss/notifications/templates/regime_transition.py`、风险过滤 | daily 工作日 |
| `bj_cache/*.csv` | 213×csv，6.9M，per-symbol 北证日线 | `scripts/scan_bj50.py`（cron `scan_bj50_daily` 17:45）+ `scripts/refresh_bj_daily.py`（手动刷新任务） | `_bj_history`（`kss_app_bridge.py:1794`），北证日线图表 | daily + on-demand |
| `etf_radar_backtest_raw.parquet` | 单文件，44K | **无写入方**（repo 内零命中） | **无读取方**（repo 内零命中） | 从不更新 |

`macro/` 三个小 json（`stock_name_index.json`/`market_strip.json`/`dailybasic_latest.json`）体量小、结构规整，理论上也够格进 Tier A；本次盘点仍归 Tier B，是因为它们是 `macro/` 增量更新流水线的副产物，跟 parquet 同批次生成、生命周期绑死——拆出来单独迁移收益不明显，U14 如果觉得值得可以单独议。

`etf_radar_backtest_raw.parquet` 在当前分支已是死文件，Tier B 分类只是"如果有一天需要它，格式对得上"，不代表它有割接价值——跟下面"死重/孤儿"类的区别只在于格式（这个是干净的 parquet，不需要额外清理决策）。

**Scope 边界提醒（不是本次盘点范围，但会影响 U14/U16 设计）：** `cs_data_*.csv`（115 个，仓库根目录，非 `storage/` 下）和 `mf_*.csv`（575 个，同样在根目录）是全系统读取最频繁的两个 Tier B 级数据集（个股日线 + 资金流），且已经反射进 `storage/data_catalog.json`（`kind: csv-glob`）。R13 的措辞明确限定"现有 `storage/` 数据资产"，这两个不在 `storage/` 下，因此严格按盘点范围不出现在上面的表里——但 U16 的 DuckDB 查询层如果不覆盖它们，"Seesaw 能查什么"这个产品面就会有个大缺口。建议 U14/U16 明确决定是否把根目录这两个 csv-glob 数据集也纳入 DuckDB catalog（大概率应该纳入，只是不需要"迁移"，因为它们已经是 Tier B 该有的格式）。

### Tier C — 保留文件（不迁移，只做展示/日志用途）

| 路径 | 格式/量级 | 说明 |
|---|---|---|
| `daily_review/*.md` | 48×md，264K | 正文见上方"待新建索引"表 |
| `reports/` 下的 md/png/csv | 见上方索引表 | |
| `etf_radar/*.commentary.md` | 见上方索引表 | |
| `news_digest/*.md` | 见上方索引表 | |
| `notes/*.md` | 见上方索引表 | |
| `logs/` | 36 文件，4.5M：`logs/cron/*.log`（每 cron suffix 一份，`rotate_cron_logs` 03:10 daily 轮转）+ `logs/sidecar.log`（Swift `BridgeClient.swift` 直读） | 无计算用途，只供人查看/调试；已是 Tier C 共识，不重新论证 |
| `intraday_quotes.db` | SQLite，88K | 自带独立表契约（`canonical_bars`/`instrument_registry`/`provider_bar_contracts`/`coverage_assessments`/`ingest_runs`/`payload_blobs`/`payload_observations`/`session_profiles`）与脱敏纪律，plan Key Decisions 已明确**不并入**统一库，本文档不重复设计 |

### 死重/孤儿——排除出割接范围（既不迁移也不主动清理，留给作者决策）

| 路径 | 量级 | 证据 |
|---|---|---|
| `kronos/` | 390M（含 1 个 `.safetensors` 模型文件 + 1 个 sqlite） | 对应的 `kss/kronos/*.py` 与 `kronos_vendor/` 在当前分支（`feat/release-hardening-settings`）与 `main` 上均不存在——功能活在未合并的 `feat/kronos-shadow-synthetic-stress` 分支（`docs/deferred/kronos-shadow-synthetic-stress.md` 已记录"严重分叉，需 cherry-pick 到新分支"）。`.gitignore:72` 已标注该目录"可由批处理重建，不入库"。当前分支唯一引用是 `build_data_catalog.py` 的被动 schema 反射，非功能性读取。磁盘上的 390M 是切换分支时的残留产物。 |
| `kss_quotes.db` | 12M | `kss/data/sqlite_store.py:SQLiteStore` 只被一次性迁移脚本 `scripts/migrate_csv_to_sqlite.py` 使用；查询 API（`load_stocks`/`load_stock`/`load_index`）在 `kss/`+`scripts/` 无任何调用方。唯一引用是 `build_data_catalog.py` 的被动 schema 反射。 |
| `industry_map.csv` | 4K | 相对活跃路径 `kss/macro/rotation.py:load_industry_map`（主源 `storage/macro/industry_map_swl1.parquet`，磁盘上**目前并不存在**，需 `scripts/backfill_industry_map.py` 手动生成；次源 `storage/stock_names.csv` 的 `industry` 列）已是 legacy——`storage/industry_map.csv` 只被 `kss/data/industry_mapping.py:IndustryMapping.from_csv` 消费，唯一非测试调用方是手工分析脚本 `scripts/analyze_kcb50_ultimate.py` 和一个 notebook。 |
| `etf_radar_backtest_raw.parquet` | 44K | 见上方 Tier B 小节——格式虽干净，但零写入零读取。 |
| `pipeline_alpha/` | 1×json，4K | 写入方 `scripts/compute_pipeline_alpha.py:250`（脚本 docstring 自称"手动测试"，`kss/config/cron_jobs.yaml` 无对应条目）。读取方：无——`scripts/refresh_factor_health.py:59` 只 import 该脚本的辅助函数，不读它写出的 json；`kss_app_bridge.py:2774` 明确注释"pipeline 权重更新是手动的，不自动读这个目录"。纯离线研究产物，跟 `notes/` 的"零读取"性质类似，但 `notes/` 至少结构规整值得进 Tier A 候补，这个连结构化收益都不明显，直接排除。 |
| `legacy/` | 24K（旧 crontab 导出 + 旧 scanner 日志） | 全仓库搜索文件名/内容零命中，`kss_app_bridge.py:3137` 注释明确"crontab 已是 legacy"，launchd 已完全取代。 |
| `.DS_Store` | 微量 | macOS Finder 元数据，非应用数据，理论上不该提交进仓库；不属于本次盘点讨论范畴，随手一提。 |

### 派生产物——不迁移，割接后重新生成

| 路径 | 说明 |
|---|---|
| `data_catalog.json` | `scripts/build_data_catalog.py` 的输出（cron `data_catalog_daily` 08:50 daily），当前反射 23 个数据集（11×parquet + cs_data/mf 两个 csv-glob + 5×sqlite + 4×dir-glob）。这不是一份"数据资产"，是对其它资产的**反射结果**——Tier A/B 割接完成后，这份文件的生成逻辑本身要跟着改（新增 Tier A 表的反射、退休已并表的旧 sqlite 路径），是 U17（"catalog 反射扩展"）的工作范围，本次盘点只记录它现在长什么样，不给它分配 Tier 或目标表。 |

**跨仓库命名提醒（不是 `storage/` 内的项，不占清单行，但对 U14 有直接影响）：** 仓库根目录还有一个独立的 `datasette/kss.db`（106M，`datasette/build_db.py` 生成，配 `datasette/serve.sh` 起本地 SQL 浏览器）——这是作者此前的一个探索性原型，跟 plan 里要新建的统一库**同名但完全是两回事**（不同路径、不同 schema、不同生命周期）。U14 设计新 `kss.db` 时如果沿用这个文件名，两者会长期共存在同一仓库里造成混淆；建议要么明确统一库落在 `STATE_ROOT/storage/kss.db`（跟 `datasette/kss.db` 路径不冲突，只是名字撞了，文档里需要一句话消歧），要么在 U14 单元里换个更明确的文件名。这不是本次盘点的阻塞项，只是一个必须在 U14 开工前看到的提醒。

## Verification 自查

`find storage -maxdepth 1 -mindepth 1 | wc -l` = 39，其中 `intraday_quotes.db-shm`/`intraday_quotes.db-wal` 是 SQLite 运行期自动生成的 WAL 侧车文件（本次盘点执行 `sqlite3 ... .tables` 探查时临时产生的），不是独立数据资产，随主库 `intraday_quotes.db` 一并归 Tier C，不单独占行。刨掉这 2 个，实际顶层资产 **37 项**，逐一核对如下，无遗漏：

- Tier A 直接表：19 项（`paper_trade` `sector_rotation` `mi_signals` `indicator_signals` `intel_radar` `intel_rewrites` `perilla_cache` `prediction_ledger` `factor_health` `indicator_registry.yaml` `indicator_lab` `pipeline_weights.json` `sector_review_config.json` `themes_15th_5y.yaml` `mi_rules.yaml` `stock_names.csv` `app_runs` `watchlist_symbols.txt` `intraday_session_cache`）
- 待新建索引域：6 项（`daily_review` `reports` `etf_radar` `news_digest` `trends` `notes`）
- Tier B：3 项（`macro` `bj_cache` `etf_radar_backtest_raw.parquet`）
- Tier C 独占（不与上面重复）：2 项（`logs` `intraday_quotes.db`）
- 死重/孤儿：5 项（`kronos` `kss_quotes.db` `industry_map.csv` `pipeline_alpha` `legacy`）
- 派生产物：1 项（`data_catalog.json`）
- 非应用数据：1 项（`.DS_Store`）

19+6+3+2+5+1+1 = **37**，与实际顶层资产数一致。每行写入方/读取方均给了 file:line 或明确的"无写入方/无读取方"结论（后者本身也是一种经核实的证据，不是遗漏）。
