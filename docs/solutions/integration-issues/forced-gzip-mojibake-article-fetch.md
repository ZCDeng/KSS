---
title: 外部站点强制 gzip 导致正文乱码入库（抓取解码防线）
date: 2026-07-22
category: integration-issues
module: kss/news/article_fetch
problem_type: integration_issue
component: service_object
symptoms:
  - "资讯雷达原文 Tab 满屏 U+FFFD 替换符（B 站热议条目最典型），标题正文全部不可读"
  - "缓存表 intel_article_items 出现 body 以 gzip magic（0x1f 0x8b 解码残渣）开头的污染行"
  - "乱码正文被判为 fulltext 并入库，之后每次点开都命中污染缓存"
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [gzip, charset, mojibake, article-fetch, bilibili, encoding, cache-pollution]
---

# 外部站点强制 gzip 导致正文乱码入库（抓取解码防线）

## Problem

资讯雷达用 stdlib urllib 抓文章正文时，对响应字节流直接按声明 charset `decode(errors="replace")`。B 站等站点对未声明 `Accept-Encoding` 的客户端**仍然强制返回 gzip**，压缩字节被硬解成文本后满屏 �，且因长度超过有效性门槛被判为 `fulltext` 写入正文缓存，此后每次点开都命中污染行。

## Symptoms

- 原文 Tab 全是 `�` 替换符，无任何可读文字
- `sqlite3 storage/kss.db "SELECT substr(body,1,30) FROM intel_article_items"` 可见以 gzip 解码残渣开头的行
- 探针确认：`curl` 不带 `Accept-Encoding` 请求 bilibili，响应头 `Content-Encoding: gzip`、body 前两字节 `1f 8b`

## What Didn't Work

- 只信 `resp.headers.get_content_charset()` 换编码重解——问题不在 charset，字节流本身是压缩数据，换任何编码都是乱码
- 指望「HTTP 客户端没请求压缩，服务器就不会压」——对大站不成立，B 站无视协商强压 gzip

## Solution

三层防线（`kss/news/article_fetch.py`，合 main 于 `a21cfe97`）：

1. **解压按 magic bytes，不信响应头**：

```python
def _decompress_body(raw: bytes, content_encoding: str | None) -> bytes:
    if raw[:2] == b"\x1f\x8b":  # gzip magic，无论头怎么说
        return gzip.decompress(raw)
    if "deflate" in (content_encoding or "").lower():
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw
```

2. **charset 三级探测链**：声明 charset → 页面 `<meta charset>`（前 2048 字节正则）→ `charset_normalizer.from_bytes()` 兜底；每级按替换符占比择优。
3. **乱码门槛防入库**：strip 后正文 `�` 占比 > 5%（`_MOJIBAKE_RATIO`）判为 `mode="empty"` + `error="undecodable body (mojibake)"`——上游走 RSS 摘要兜底，且读穿缓存只写 `fulltext`，乱码永远进不了库。

已污染行一次性清洗：`DELETE FROM intel_article_items WHERE body LIKE '%�%' OR length(body) < 80`。

## Why This Works

根因是两个隐含假设都不成立：响应体未压缩、声明 charset 可信。按 magic bytes 解压消除第一个假设对服务器行为的依赖；探测链消除第二个（顺带修好 GBK 老站）；乱码门槛是最后一道闸——即使前两层都失手，不可读内容也只会降级为摘要兜底，不会以「全文」身份持久化污染缓存。

## Prevention

- 接任何新外部源前先拉真实响应核字节头与响应头（`Content-Encoding` / magic bytes），不要只看浏览器渲染效果——浏览器自动解压，curl/urllib 不会
- 任何「抓取 → 持久化」链路都要有**不可读内容门槛**：入库前检查替换符占比 / 最小长度，宁可降级兜底也不缓存垃圾（缓存无 TTL 时污染是永久的）
- 回归测试三件套（`kss/tests/test_article_fetch.py`）：gzip 强压响应解压、错误声明 charset 下的 meta 探测、乱码门槛拒绝入 fulltext

## Related Issues

- plan：`docs/plans/2026-07-22-001-feat-intel-reader-experience-plan.md`（U1/U2 正文提取与缓存，本 bug 为真机走查发现）
- 同源纪律：先验数据源再写代码（接外部源前先拉真实响应核字段/粒度）
