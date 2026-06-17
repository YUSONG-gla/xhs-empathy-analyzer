# HEART 共情力评分系统后端

基于 **FastAPI + LLM** 的文本共情力量化评估后端服务。用户提交待评估文本，系统通过大语言模型按 HEART 框架从 9 个维度进行量化打分，返回加权总分与综合评价。

---

## 功能特性

| 功能 | 说明 |
|------|------|
| 9 维度评分 | 情感生动性、环境生动性、角色脆弱性等 9 个维度 |
| 加权总分计算 | 按指定权重公式计算 (0, 10] 区间共情度总分 |
| 双 LLM 提供商 | 支持 OpenAI（GPT-4o）和 Google Gemini，一键切换 |
| 四层 JSON 容错 | 应对 LLM 返回的非标准/破损 JSON |
| 频率限制 | 每 IP 每分钟最多 20 次请求 |
| 请求追踪 | 每个请求自动注入 `X-Request-ID` 响应头 |
| CORS 支持 | 可被微信小程序等前端跨域调用 |

---

## 加权公式

```
共情度 = 情感生动性×0.50 + 环境生动性×0.15 + 角色脆弱性×0.10
        + 认知表述丰富度×0.08 + 语气情绪×0.05 + 情节体量×0.04
        + 矛盾解决程度×0.03 + 角色发展程度×0.03 + 情绪转变程度×0.02
```

---

## 项目结构

```
backend/
├── main.py                     # FastAPI 应用入口
├── requirements.txt            # 依赖清单
├── .env.example                # 环境变量模板
├── .gitignore
│
├── config/
│   └── settings.py             # 配置中心（pydantic-settings）
│
├── models/
│   └── schema.py               # Pydantic 数据模型 & 常量
│
├── api/
│   ├── routes.py               # API 路由层
│   └── dependencies.py         # 依赖注入：频率限制、请求 ID 中间件
│
├── services/
│   ├── prompt_builder.py       # HEART 框架 Prompt 构建
│   ├── llm_service.py          # LLM 调用服务（OpenAI / Gemini）
│   └── scorer.py               # 评分主流程编排
│
├── utils/
│   ├── calculator.py           # 加权计算器（纯函数）
│   └── parser.py               # 四层容错 JSON 解析器
│
└── tests/
    ├── test_calculator.py      # 计算器单元测试
    ├── test_parser.py          # 解析器单元测试
    └── test_api.py             # API 集成测试（Mock LLM）
```

---

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 API Key
```

`.env` 关键配置项：

```ini
LLM_PROVIDER=openai          # 切换为 gemini 使用 Google Gemini
OPENAI_API_KEY=sk-...        # OpenAI API Key
OPENAI_MODEL=gpt-4o
OPENAI_BASE_URL=https://api.openai.com/v1   # 可配置代理地址
```

### 3. 启动服务

```bash
uvicorn main:app --reload --port 8000
```

启动后访问：
- Swagger UI：http://localhost:8000/docs
- ReDoc：http://localhost:8000/redoc

---

## API 文档

### POST `/api/score` — 文本共情力评分

**请求体**

```json
{
  "text": "今天走在雨中，街灯的倒影在积水中晃动，我突然想起了她说过的话..."
}
```

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `text` | string | 10 ~ 10000 字符 | 待评分的文本内容 |

**成功响应 (200)**

```json
{
  "success": true,
  "dimensions": [
    {
      "name": "情感生动性",
      "key": "vividness_emotion",
      "score": 10.0,
      "reason": "文本通过雨中街灯等意象细腻传达了失落情绪，情感表达极为生动。"
    }
    // ... 共 9 个维度
  ],
  "total_score": 7.82,
  "calculation_process": "情感生动性(10) * 0.5 = 5.0 + 环境生动性(6) * 0.15 = 0.9 + ... = 7.82",
  "evaluation": "该文本在情感描摹上十分出色，通过具体场景有效唤起读者共情...",
  "model_used": "gpt-4o"
}
```

**错误响应**

| 状态码 | 场景 |
|--------|------|
| 422 | 请求体格式错误（文本为空/过短/过长） |
| 429 | 请求频率超限（每分钟 20 次/IP） |
| 502 | LLM 调用失败或输出解析失败 |
| 500 | 服务器内部错误 |

### GET `/health` — 健康检查

```json
{"status": "ok", "service": "heart-scorer", "version": "1.0.0-MVP"}
```

---

## 运行测试

```bash
cd backend
pytest tests/ -v
```

无需配置真实 API Key，测试通过 Mock 模拟 LLM 调用。

---

## 切换 LLM 提供商

修改 `.env` 中的 `LLM_PROVIDER` 字段：

| 值 | 提供商 | 对应模型配置 |
|----|--------|-------------|
| `openai` | OpenAI | `OPENAI_MODEL`, `OPENAI_API_KEY` |
| `gemini` | Google Gemini | `GEMINI_MODEL`, `GEMINI_API_KEY` |
