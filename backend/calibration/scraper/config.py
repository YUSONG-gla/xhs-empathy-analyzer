"""
爬虫配置：UA 池、引擎参数、冷却策略、采集关键词。

合规说明：
- 仅采集公开可见帖子（标题、正文、点赞数），不采集用户隐私信息
- 数据仅用于内部模型训练，不对外公开原始内容
- 严格遵守限速与冷却策略，避免对目标站点造成压力
"""

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# User-Agent 池：覆盖不同浏览器/系统组合，降低指纹聚类风险
# ---------------------------------------------------------------------------
USER_AGENTS = [
    # Chrome - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Edge - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    # Safari - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    # Firefox - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
    "Gecko/20100101 Firefox/124.0",
]

# 视口尺寸池：随引擎实例固定分配，模拟不同设备
VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
]

# 中文地区 locale / 时区，避免地理位置指纹异常
LOCALES = ["zh-CN"]
TIMEZONES = ["Asia/Shanghai"]


@dataclass
class CooldownConfig:
    """单个引擎实例的冷却策略。"""

    # 单引擎连续工作多少次请求后强制冷却
    max_requests_before_cooldown: int = 8
    # 冷却时长范围（秒），随机取值避免规律性
    cooldown_seconds_range: tuple[int, int] = (60, 150)
    # 每次请求之间的随机延迟范围（秒），模拟人类阅读/操作间隔
    action_delay_range: tuple[float, float] = (2.0, 5.5)
    # 单引擎使用次数达到该上限后，整个浏览器上下文销毁重建（更换指纹）
    max_requests_before_recreate: int = 40


@dataclass
class EnginePoolConfig:
    """多引擎（多浏览器上下文）池配置。"""

    # 同时维护的引擎（浏览器上下文）数量，轮询分配采集任务
    pool_size: int = 3
    headless: bool = False
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)


@dataclass
class ScrapeTask:
    """一次采集任务：关键词 + 目标分类 + 目标数量。"""

    keyword: str
    category: str
    target_count: int


# 本周采集计划：覆盖不同情感强度/赛道，对应 Sprint 1 任务
SCRAPE_PLAN: list[ScrapeTask] = [
    ScrapeTask("青春随笔", "情感故事", 60),
    ScrapeTask("治愈文案", "情感故事", 60),
    ScrapeTask("成长感悟", "情感故事", 30),
    ScrapeTask("一个人旅行", "旅行游记", 50),
    ScrapeTask("今日份分享", "日常分享", 50),
    ScrapeTask("考研日记", "学习职场", 50),
]

OUTPUT_CSV = "calibration/data/raw_real_texts.csv"

# 登录态存储路径：由 save_login_state.py 生成，xhs_scraper.py 启动引擎时加载
LOGIN_STATE_PATH = "calibration/scraper/login_state.json"

# 广告/软文过滤关键词（命中即丢弃，采集端先过一层粗筛）
AD_KEYWORDS = ["下单", "链接戳", "券后价", "佣金", "代理加盟", "私信我"]

# 正文长度过滤（字符数）
MIN_TEXT_LENGTH = 50
MAX_TEXT_LENGTH = 600

# ---------------------------------------------------------------------------
# 混合采集架构配置
# 列表页：必须用 Playwright（接口有 x-s/x-s-common 签名校验，httpx 无法直连，
#         只能让真实浏览器的 JS 自己生成签名，我们拦截网络响应读取结果）
# 详情页：用 httpx 直接请求 SSR 页面，解析 window.__INITIAL_STATE__（无签名要求）
# ---------------------------------------------------------------------------

# 搜索接口 URL 片段，用于 Playwright 响应监听时匹配目标请求。
# 实测发现：不同入口方式触发的是不同版本的接口——
#   AI搜索框输入触发 → so.xiaohongshu.com/api/sns/web/v2/search/notes
#   直接打开搜索结果URL触发 → edith.xiaohongshu.com/api/sns/web/v1/search/notes
# 用不带版本号的通用片段，兼容两种情况。
SEARCH_API_URL_PATTERN = "/search/notes"

# 详情页 httpx 请求之间的随机延迟范围（秒），模拟人类阅读节奏
# 实测：约90次请求在13分钟内（平均间隔~9秒）就触发了风控（详情页全部302到登录页），
# 因此延迟和冷却阈值都调得更保守。
DETAIL_FETCH_DELAY_RANGE: tuple[float, float] = (6.0, 12.0)

# 详情页 httpx 请求累计多少次后休息一下（轻量级冷却，httpx 请求成本远低于
# Playwright 导航，但仍需要避免短时间内对同一账号高频请求详情接口）
DETAIL_FETCH_COOLDOWN_EVERY = 10
DETAIL_FETCH_COOLDOWN_SECONDS_RANGE: tuple[float, float] = (60.0, 120.0)
