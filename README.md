# 考勤跨夜班与加班分段核算

面向制造企业薪酬核算员，处理采信、判定、核算、复核与归档。

## 启动

```bash
docker compose up --build -d
```

访问 http://localhost:18252 。初次启动自动运行脏样例，可切换 clean.csv 与 legacy.csv。顶部“运行批次”会重新计算，同源输入复用批次。

## 业务模块

- 打卡采信：`/m1_ingest`、`/api/m1_ingest`
- 班次日历：`/m2_mapping`、`/api/m2_mapping`
- 跨夜配对：`/m3_versions`、`/api/m3_versions`
- 缺卡复核：`/m4_segments`、`/api/m4_segments`
- 工时切段：`/m5_calculate`、`/api/m5_calculate`
- 薪资对账：`/m6_reconcile`、`/api/m6_reconcile`
- 周期归档：`/m7_archive`、`/api/m7_archive`
- 期间核算：`/period`、`/api/intake`、`/api/period`

## 核心规则

跨夜按班次归属日配对，休息区间先扣除，再按法定日期和工时阈值切分倍率。完整数值、边界和异常定义见 `docs/DATA-DICTIONARY.md`。

交接班到件分两层处理：每个到件文件当天登记为独立接收批次（来源、哈希、接收时间、行数对账），当天可查；但不按单文件出核算结果。以班次（员工×归属日）为单位跨文件合并进出卡，补齐才进入期间核算结果出工资；单边卡班次标记等卡中，不计金额、不隔离整班、不阻塞其他班次。同一来源内容更新后旧接收批次标记已取代、旧期间结果失效并重算；同一来源同一内容重复提交幂等命中，不新增批次或金额。`/period` 区分已可核算与等卡中班次，`/api/period/export.csv` 导出含接收、班次、计算、隔离、对账记录，可回查来源。

## 验证

```bash
docker compose exec -T app pytest -q
docker compose config --quiet
```

原始样例在 `sample_data/`。测试在 `tests/`。封存文件是单份CSV，含运行清单、计算证据、隔离原因与对账。复核需提交当前版本，陈旧复核返回409。

## 数据与限制

SQLite保存在records命名卷，普通重启与构建不会清除。确认可丢弃本项目数据后，运行 `docker compose down -v` 再启动可重置。单文件上限2MB；不接入外部业务系统；历史规则由本项目样例定义，不代表实际行业标准。
