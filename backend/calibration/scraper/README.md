# 小红书文本采集脚本

用于 Sprint 1（文本语料采集）。采集公开搜索结果中的标题/正文/点赞数，仅用于内部模型训练。

## 架构说明（混合模式）

小红书搜索接口（`/api/sns/web/v2/search/notes`）带 `x-s`/`x-s-common` 签名校验，httpx 无法直连模拟；
但笔记详情页是普通 SSR 页面，没有签名要求。因此采用混合架构：

```
列表页：Playwright 打开搜索页，拦截浏览器自己发出的搜索接口响应，直接读取原始 JSON
        （不依赖 CSS 选择器，不怕页面改版；签名由真实浏览器的 JS 自动生成）
    ↓
详情页：httpx 直接请求 SSR 页面，解析 window.__INITIAL_STATE__ 拿正文全文
        （轻量请求，不需要渲染浏览器，比 Playwright 快得多）
```

对应模块：

| 模块 | 作用 |
|------|------|
| `search_capture.py` | 列表页采集：监听 `page.on("response")`，解析搜索接口 JSON |
| `http_detail_client.py` | 详情页采集：httpx + cookie 复用 + `__INITIAL_STATE__` 解析 |
| `engine_pool.py` | Playwright 浏览器上下文池（UA 轮换/冷却/重建），仅用于列表页 |
| `xhs_scraper.py` | 主流程编排，整合以上模块 + CSV 写入 |

两个核心字段路径均已用真实抓包数据验证：
- 列表页：`payload["data"]["items"][i]`（注意 `model_type` 可能是 `"rec_query"` 相关搜索推荐，已在代码里过滤）
- 详情页：`state["note"]["noteDetailMap"][note_id]["note"]["desc"]` / `["interactInfo"]["likedCount"]`

## 安装

```bash
cd heart/backend
pip install -r requirements.txt
playwright install chromium
```

## 登录态（必须先做，搜索接口需要登录身份）

```bash
python -m calibration.scraper.save_login_state
```

会打开一个真实浏览器窗口并跳转到小红书首页，**在窗口里手动扫码或输入账号密码登录**，登录成功、能正常看到首页内容后，回到终端按回车，登录态会保存到 `calibration/scraper/login_state.json`。

这个登录态会被同时用于：
- `engine_pool.py`：Playwright 创建浏览器上下文时加载（列表页）
- `http_detail_client.py`：转换成 cookie dict 喂给 httpx（详情页）

> 登录态有一定有效期，如果某天发现大量笔记被判定"不存在/无法查看"，先检查是否触发了账号风控（参考下方"踩坑记录"），而不是急于重新登录。

## 运行

```bash
python -m calibration.scraper.xhs_scraper
```

输出：`calibration/data/raw_real_texts.csv`，支持中断后重新运行自动跳过已采集标题（去重）。

## 反爬与稳健性机制

| 机制 | 实现位置 | 作用 |
|------|----------|------|
| UA 身份轮换 | `config.py: USER_AGENTS` + `engine_pool.py: _create_engine` | 每个引擎固定一个 UA，互不相同 |
| 多引擎轮询 | `engine_pool.py: EnginePool` | 列表页请求在 N 个浏览器上下文间轮询 |
| 引擎冷却/重建 | `config.py: CooldownConfig` | 单引擎达到阈值后休眠或销毁重建（更换指纹） |
| 反检测脚本注入 | `engine_pool.py: _create_engine` | 隐藏 `navigator.webdriver` 等自动化特征 |
| 详情页轻量冷却 | `config.py: DETAIL_FETCH_*` | httpx 请求累计一定次数后主动休息 |
| 404/不可用检测 | `http_detail_client.py: fetch_detail` | 笔记不存在时直接判定跳过，不空等超时 |
| 内容粗筛 | `xhs_scraper.py: is_ad / passes_length_filter` | 过滤广告与长度异常文本 |

## 踩坑记录（重要）

- **`error_code=300031`（"当前笔记暂时无法浏览"）**：这是小红书**账号级风控**，不是代码 bug。无论用 Playwright 还是 httpx，只要带的是同一个被限制账号的 cookie，结果一样。遇到这种情况应停止运行，更换账号或等待冷却，而不是继续调代码。
- **搜索结果不在 SSR HTML 里**：`search_result?keyword=...` 页面的 `__INITIAL_STATE__.search.feeds` 始终是空数组，列表数据是页面加载后异步请求的，因此列表页必须用 Playwright 拦截网络响应，不能像详情页一样直接 httpx 解析 HTML。

## 调整采集计划

编辑 `config.py` 中的 `SCRAPE_PLAN` 列表，修改关键词、分类、目标数量。
