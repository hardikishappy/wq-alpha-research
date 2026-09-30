---
name: wq-alpha-research
description: "Use for WorldQuant BRAIN alpha research: designing WQ Alpha expressions, selecting fields/operators, diagnosing simulation and IS check failures, tuning Sharpe/Fitness/Turnover, submitting alphas, and building low-correlation alpha portfolios. Also use for 中文 requests about WorldQuant、BRAIN、WQ Alpha、因子表达式、回测、提交、换手、Fitness、Sharpe."
---

# WQ Alpha 研究 Skill

> 结构化 playbook：字段 → 表达式 → 回测 → 检查 → 提交 → 组合。融合 WorldQuant BRAIN 文档知识与 USA TOP3000 实证经验。

---

## 1. 快速决策树

```
开始
  ├── 拉取所有 alpha 列表 ──→ 只看 ACTIVE；算 **日收益** 相关，>0.7 则修改或放弃
  ├── 设计新因子
  │    ├── 字段已验证？ ──否──→ 查第 2 节（本地字段文件搜索 / 模拟 rank(field)）
  │    └── 是
  │         ├── 基本面 ──→ group_rank + ts_rank, SUBINDUSTRY, decay=0
  │         ├── 分析师 ──→ group_rank + ts_rank, INDUSTRY/SUBINDUSTRY, decay=0–4
  │         ├── 技术 ────→ 高 decay(10–30) 或混合基本面降低换手
  │         └── 情绪 ────→ nanHandling=ON, 小窗口谨慎
  └── 提交后 ──→ 验证 status == ACTIVE，否则检查 SELF_CORRELATION
```

---

## 2. 字段速查（本地数据集）

本 SKILL 已内置 USA TOP3000 delay=1 的完整字段列表（共 4367 个），无需每次从网页/ API 拉取：

- `references/wq_usa_top3000_delay1_data_fields.json`：完整字段元数据数组
- `references/wq_usa_top3000_delay1_data_fields.csv`：CSV 版，方便 Excel/ pandas 查看
- `references/wq_usa_top3000_delay1_data_fields_summary.json`：分类统计与示例字段

字段分布：

| 类别 | 数量 | 说明 |
|------|------|------|
| fundamental | 1652 | 财务报表、附注科目 |
| analyst | 1324 | 分析师预期、一致预期 |
| news | 996 | 新闻、财报事件 |
| pv | 195 | 价量、ADV、VWAP 等 |
| option | 138 | 期权隐含波动、Put/Call 等 |
| model | 40 | 模型因子 |
| socialmedia | 22 | 社交媒体情绪 |
| univ1 | 6 | Universe 相关 |

### 2.1 本地搜索字段

```python
import json
from pathlib import Path

# 假设在 skill 目录下运行；如在其他位置，改为实际路径
skill_dir = Path(".")
field_dir = skill_dir / "references"
data = json.loads((field_dir / "wq_usa_top3000_delay1_data_fields.json").read_text(encoding="utf-8"))

keyword = "operating_income"
matches = [
    f for f in data
    if keyword.lower() in f["id"].lower()
    or (f.get("description") and keyword.lower() in f["description"].lower())
]

for f in matches[:10]:
    print(f"{f['id']} | {f.get('category',{}).get('name')} | {f.get('dataset',{}).get('name')} | coverage={f.get('coverage')} | alphaCount={f.get('alphaCount')}")
```

### 2.2 按类别筛选

```python
category = "pv"  # 或 fundamental / analyst / news / option / model / socialmedia
fields = [f for f in data if f.get("category", {}).get("id") == category]
print(f"{category}: {len(fields)} fields")
for f in sorted(fields, key=lambda x: x.get("alphaCount", 0), reverse=True)[:10]:
    print(f"  {f['id']} | alphaCount={f.get('alphaCount')} | coverage={f.get('coverage')}")
```

### 2.3 字段验证

拿到候选字段后，**先用简单表达式模拟验证**字段是否真的可用：

```python
payload = {
    "type": "REGULAR",
    "settings": {
        "instrumentType": "EQUITY", "region": "USA", "universe": "TOP3000",
        "delay": 1, "decay": 0, "neutralization": "MARKET",
        "truncation": 0.08, "pasteurization": "ON", "unitHandling": "VERIFY",
        "nanHandling": "ON", "language": "FASTEXPR", "visualization": False,
    },
    "regular": "rank(my_candidate_field)",
}
resp = session.post("https://api.worldquantbrain.com/simulations", json=payload)
# 201 表示字段可用；非 201 通常表示字段不存在或参数不匹配
```

### 2.4 何时需要重新拉取

本地字段集已覆盖 USA TOP3000 delay=1。以下情况才需要重新从 BRAIN 拉取：

- 换 Region（如 CHN、EUR）
- 换 Universe（如 TOP500、TOP1000）
- 换 Delay（如 0）
- BRAIN 平台字段列表明显更新（可对比 `dateCreated` 与本地）

---

## 3. 运算符速查表

| 类型 | 算子 | 作用 |
|------|------|------|
| 截面 | `rank(x)`, `zscore(x)`, `normalize(x)`, `scale(x)`, `winsorize(x, std=4)` | 每天对所有股票标准化 |
| 时序 | `ts_mean`, `ts_std_dev`, `ts_delta`, `ts_rank`, `ts_corr`, `ts_decay_linear`, `ts_backfill`, `ts_zscore` | 单只股票历史窗口计算 |
| 分组 | `group_rank(x, group)`, `group_neutralize(x, group)`, `group_zscore(x, group)`, `group_backfill(x, group, N)` | 组内中性化 |
| 条件 | `if_else(cond, a, b)`, `trade_when(x, cond, delay)` | 条件暴露 |
| 向量 | `vec_avg(a, b, c)`, `vec_sum(a, b, c)` | 多字段逐元素平均/求和 |

**黄金组合**：`group_rank(ts_rank(signal, N), subindustry)`

---

## 4. 因子模板库

### 4.1 高胜率模板

```fastexpr
-- 模板 A：ROE 趋势（通过率最高）
group_rank(ts_rank(operating_income / equity, 126), subindustry)

-- 模板 B：EPS 收益率修正
group_rank(ts_rank(est_eps / close, 126), industry)

-- 模板 C：FCF 收益率
group_rank(ts_rank(free_cash_flow_reported_value / equity, 126), industry)

-- 模板 D：多因子混合（高 Fitness）
0.5 * group_rank(ts_rank(operating_income / equity, 126), subindustry)
+ 0.5 * group_rank(ts_rank(est_eps / close, 126), industry)

-- 模板 E：低相关技术+基本面混合
0.5 * rank(-(close / open - 1)) + 0.5 * rank(ts_rank(operating_income / equity, 126))

-- 模板 F：资产周转 × 利润率
rank(ts_rank(operating_income / sales * sales / assets, 126))
```

### 4.2 推荐默认设置

| 因子类型 | Decay | Neutralization | Truncation | nanHandling | 预期 TO |
|----------|-------|----------------|------------|-------------|---------|
| 基本面质量 | 0 | SUBINDUSTRY | 0.08 | ON | 2–8% |
| 分析师预期 | 0–4 | INDUSTRY/SUBINDUSTRY | 0.08 | ON | 9–16% |
| 技术反转 | 10–30 | INDUSTRY | 0.08 | OFF | 15–35% |
| 混合因子 | 4–20 | INDUSTRY/SUBINDUSTRY | 0.08 | ON | 10–20% |
| 情绪 | 4–10 | INDUSTRY | 0.05–0.08 | ON | 8–30% |

---

## 5. 指标与检查

### 5.1 核心指标

| 指标 | 公式/含义 | 目标 |
|------|-----------|------|
| Sharpe | 日 IR × √252 | ≥ 1.5（最低 1.25） |
| Fitness | Sharpe × √(|Returns| / max(TO, 0.125)) | ≥ 1.1（最低 1.0） |
| Returns | 年化收益 / $10M | ≥ 7% |
| Turnover | 日交易额 / Book Size | 1%–20% |
| Drawdown | 峰值到谷值最大回撤 | < 15% |
| Margin | PnL / 总交易额 | 越高越好 |

### 5.2 IS 检查清单

| 检查项 | 阈值 | 失败原因 | 修复方法 |
|--------|------|----------|----------|
| LOW_SHARPE | ≥ 1.25 | 信号弱 | 换字段/窗口/加 group_rank |
| LOW_FITNESS | ≥ 1.0 | 换手过高 | 增大 decay、混合稳定信号 |
| LOW_TURNOVER | ≥ 1% | 信号太稳定 | 缩短窗口、换更活跃字段 |
| HIGH_TURNOVER | ≤ 70% | 换手爆炸 | 增大 decay、trade_when、混合 |
| CONCENTRATED_WEIGHT | 单股 < 10% 且分散 | 权重集中 | 用 rank()、降低 truncation、ts_backfill |
| LOW_SUB_UNIVERSE_SHARPE | TOP1000 也有效 | 小票依赖 | 用基本面、SUBINDUSTRY、避免市值倾斜 |
| SELF_CORRELATION | **日收益** 相关系数 < 0.7 | 与已有因子太像 | 换信号簇、加过滤、换 Universe；不要只调参数 |
| MATCHES_COMPETITION | 信息性 | — | 无影响 |

### 5.3 失败统计

| 失败原因 | 占比 | 结论 |
|----------|------|------|
| LOW_SHARPE | 90.7% | 信号质量是最大瓶颈 |
| LOW_FITNESS | 66.2% | 通常是 HIGH_TURNOVER 的软性版本 |
| LOW_SUB_UNIVERSE_SHARPE | 51.0% | 避免小票/流动性倾斜 |

**按数据类型通过率**：基本面 40% > 混合 12.7% > 纯技术 5.3% > 其他 0%

---

## 6. 问题诊断与修复

| 症状 | 可能原因 | 修复 |
|------|----------|------|
| Fitness < 1.0 | 换手 > 30% | 增大 decay、混合基本面、ts_decay_linear |
| Sharpe < 1.25 | 信号弱 | 拉长窗口、group_rank、换字段 |
| TO > 50% | 信号变化太快 | decay 10–30、trade_when、混合 |
| DD > 15% | 波动大/杠杆高 | 增大 decay、降 truncation、混合低波信号 |
| CONCENTRATED_WEIGHT FAIL | 稀疏/极值 | rank()、truncation 0.05、ts_backfill |
| Sub-Universe FAIL | 小票依赖 | 避免 `rank(-assets)`，用 group_rank、加流动性过滤 |
| simulation_error | 字段不存在/算子参数错误 | 先 rank(field) 验证字段，检查算子参数个数 |
| trade_when 零交易 | 条件过严 | 放宽条件或用 if_else |

---

## 7. BRAIN API 自动化

### 7.1 认证（请填写账号）

**使用前必须准备凭据**。推荐使用环境变量；也可以在本地放置未跟踪的 `credential.txt`（已被 `.gitignore` 忽略），内容为 JSON 数组：

```json
["your_username", "your_password"]
```

⚠️ **提醒**：不要把真实账号密码写入仓库。优先使用 `WQ_BRAIN_USERNAME` / `WQ_BRAIN_PASSWORD` 环境变量。

```python
import json
import requests
from requests.auth import HTTPBasicAuth

API_BASE = "https://api.worldquantbrain.com"

# 1. 读取 credential.txt
import os

username = os.getenv("WQ_BRAIN_USERNAME")
password = os.getenv("WQ_BRAIN_PASSWORD")
if not (username and password):
    with open("credential.txt") as f:
        username, password = json.load(f)

# 2. 创建会话并认证
session = requests.Session()
session.auth = HTTPBasicAuth(username, password)
session.headers.update({
    "Content-Type": "application/json",
    "Accept": "application/json",
})

resp = session.post(f"{API_BASE}/authentication")
assert resp.status_code == 201, f"认证失败: {resp.status_code} {resp.text}"
print("认证成功")
```

### 7.2 获取已提交 Alpha 并计算相关性

**目的**：在新因子提交前，避免与已有因子 PnL 高度相关（相关系数 ≥ 0.7）。

```python
import numpy as np

def fetch_pnl(session, alpha_id):
    """获取 Alpha 累计 PnL 序列；schema.properties 可能是 list 或 dict。"""
    r = session.get(f"{API_BASE}/alphas/{alpha_id}/recordsets/pnl")
    if r.status_code != 200 or not r.text.strip():
        return []
    data = r.json()
    props = data.get("schema", {}).get("properties", [])
    if isinstance(props, list):
        date_idx = next((i for i, p in enumerate(props) if p.get("name", "").lower() == "date"), 0)
        pnl_idx = next((i for i, p in enumerate(props) if p.get("name", "").lower() in ("pnl", "cum_pnl", "returns", "ret")), 1)
    else:
        date_idx = next((v["index"] for k, v in props.items() if k.lower() == "date"), 0)
        pnl_idx = next((v["index"] for k, v in props.items() if k.lower() in ("pnl", "cum_pnl", "returns", "ret")), 1)
    records = sorted(data.get("records", []), key=lambda r: r[date_idx])
    out = []
    for row in records:
        rec = row[0] if isinstance(row, list) and len(row) == 1 and isinstance(row[0], list) else row
        try:
            out.append(float(rec[pnl_idx]))
        except Exception:
            continue
    return out

def daily_returns(cum_pnl):
    """累计 PnL 转日收益；相关性应基于日收益，而非累计曲线。"""
    return [cum_pnl[i+1] - cum_pnl[i] for i in range(len(cum_pnl) - 1)]

def get_active_alphas(session, user_id="self", limit=100):
    """获取所有 alpha（含 ACTIVE / UNSUBMITTED），分页。"""
    all_alphas = []
    offset = 0
    while True:
        data = session.get(f"{API_BASE}/users/{user_id}/alphas", params={"limit": limit, "offset": offset}).json()
        batch = data.get("results", data.get("alphas", []))
        if not batch:
            break
        all_alphas.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return all_alphas

# 计算新因子与所有 ACTIVE alpha 的日收益相关性
new_pnl = fetch_pnl(session, new_alpha_id)
new_ret = daily_returns(new_pnl)
existing = get_active_alphas(session)
active = [a for a in existing if a.get("status") == "ACTIVE"]

high_corr = []
for alpha in active:
    old_id = alpha.get("id")
    try:
        old_pnl = fetch_pnl(session, old_id)
        old_ret = daily_returns(old_pnl)
        if len(new_ret) == len(old_ret) and len(new_ret) > 20:
            corr = float(np.corrcoef(new_ret, old_ret)[0, 1])
            print(f"与 {old_id} 日收益相关性: {corr:.3f}")
            if abs(corr) >= 0.7:
                high_corr.append((old_id, corr))
    except Exception:
        continue

if high_corr:
    print(f"⚠️ 发现 {len(high_corr)} 个高相关因子，建议修改或放弃")
```

**判断规则（基于日收益，不是累计 PnL）**：

| 相关系数 | 动作 |
|----------|------|
| abs(corr) < 0.5 | ✅ 可提交 |
| 0.5 ≤ abs(corr) < 0.7 | ⚠️ 谨慎，需提升 Sharpe 或修改信号 |
| abs(corr) ≥ 0.7 | ❌ 放弃或重构（除非新因子 Sharpe ≥ 旧因子 × 1.1） |

> ⚠️ **不要用累计 PnL 算相关**。累计曲线自带强趋势，会把不同信号的相关性严重夸大。

### 7.3 回测

```python
payload = {
    "type": "REGULAR",
    "settings": {
        "instrumentType": "EQUITY", "region": "USA", "universe": "TOP3000",
        "delay": 1, "decay": 0, "neutralization": "SUBINDUSTRY",
        "truncation": 0.08, "pasteurization": "ON", "unitHandling": "VERIFY",
        "nanHandling": "ON", "language": "FASTEXPR", "visualization": False,
    },
    "regular": "group_rank(ts_rank(operating_income/equity, 126), subindustry)",
}
resp = session.post("https://api.worldquantbrain.com/simulations", json=payload)
sim_id = resp.headers["Location"].rstrip("/").split("/")[-1]

while True:
    data = session.get(f"https://api.worldquantbrain.com/simulations/{sim_id}").json()
    if data.get("status") == "COMPLETE":
        alpha_id = data["alpha"]
        break
    time.sleep(8)

alpha = session.get(f"https://api.worldquantbrain.com/alphas/{alpha_id}").json()
```

### 7.4 提交与监控

```python
# 提交
sub = session.post(f"https://api.worldquantbrain.com/alphas/{alpha_id}/submit")
print(sub.status_code)  # 201 成功

# 监控 SELF_CORRELATION
for _ in range(30):
    alpha = session.get(f"https://api.worldquantbrain.com/alphas/{alpha_id}").json()
    sc = next((c for c in alpha.get("is", {}).get("checks", []) if c["name"] == "SELF_CORRELATION"), {})
    if sc.get("result") in ("PASS", "FAIL"):
        break
    time.sleep(60)
```

### 7.5 自动提交模板

```python
import numpy as np

def simulate_and_submit(expression, settings, existing_pnls=None):
    """
    existing_pnls: {alpha_id: [cum_pnl_values]}，已上线因子的累计 PnL 序列。
    返回: {"alpha_id": ..., "decision": "submitted|skip|high_corr|verify_failed", ...}
    """
    payload = {"type": "REGULAR", "settings": settings, "regular": expression}
    resp = session.post("https://api.worldquantbrain.com/simulations", json=payload)
    if resp.status_code != 201:
        return {"error": "simulate_failed"}
    sim_id = resp.headers["Location"].rstrip("/").split("/")[-1]
    while True:
        data = session.get(f"https://api.worldquantbrain.com/simulations/{sim_id}").json()
        if data.get("status") == "COMPLETE":
            alpha_id = data["alpha"]
            break
        if data.get("status") in ("ERROR", "FAILED"):
            return {"error": "simulation_error"}
        time.sleep(8)
    alpha = session.get(f"https://api.worldquantbrain.com/alphas/{alpha_id}").json()
    is_ = alpha.get("is", {})

    # 1. 基础指标过滤
    if is_.get("fitness", 0) < 1.1 or is_.get("sharpe", 0) < 1.3 or is_.get("turnover", 1) > 0.20:
        return {"alpha_id": alpha_id, "decision": "skip", "reason": "metrics", "metrics": is_}

    # 2. 相关性检查（基于日收益）
    def daily_rets(cum):
        return [cum[i+1] - cum[i] for i in range(len(cum) - 1)]

    if existing_pnls:
        new_pnl = fetch_pnl(session, alpha_id)
        new_ret = daily_rets(new_pnl)
        for old_id, old_pnl in existing_pnls.items():
            old_ret = daily_rets(old_pnl)
            if len(new_ret) == len(old_ret) and len(new_ret) > 20:
                corr = abs(float(np.corrcoef(new_ret, old_ret)[0, 1]))
                if corr >= 0.7:
                    # 例外：新 Sharpe 高于旧 Sharpe 10% 以上可提交
                    old_sharpe = None  # 需从外部传入或缓存
                    if old_sharpe is None or is_.get("sharpe", 0) < old_sharpe * 1.1:
                        return {"alpha_id": alpha_id, "decision": "high_corr", "corr_with": old_id, "corr": corr}

    # 3. 提交
    sub = session.post(f"https://api.worldquantbrain.com/alphas/{alpha_id}/submit")
    if sub.status_code not in (200, 201):
        return {"alpha_id": alpha_id, "decision": "submit_failed", "status": sub.status_code}

    # 4. 验证是否真正上线（BRAIN 可能因 SELF_CORRELATION 保持 UNSUBMITTED）
    for _ in range(20):
        time.sleep(10)
        alpha = session.get(f"https://api.worldquantbrain.com/alphas/{alpha_id}").json()
        if alpha.get("status") == "ACTIVE":
            return {"alpha_id": alpha_id, "decision": "submitted", "status": "ACTIVE"}
        sc = next((c for c in alpha.get("is", {}).get("checks", []) if c["name"] == "SELF_CORRELATION"), {})
        if sc.get("result") == "FAIL":
            return {"alpha_id": alpha_id, "decision": "self_correlation_fail", "status": alpha.get("status")}

    return {"alpha_id": alpha_id, "decision": "verify_failed", "status": alpha.get("status")}
```

### 7.6 限流

- 模拟/提交间 sleep 2–5 秒。
- 遇 429 读取 `Retry-After`，指数退避。
- 批量建议单线程或 ≤ 2 并发。

### 7.7 提交后验证（201 ≠ 已上线）

`POST /alphas/{id}/submit` 返回 201 只表示请求被接受，**不代表 alpha 已变为 ACTIVE**。实战中常见：

- alpha 状态仍为 `UNSUBMITTED`（SELF_CORRELATION 未通过或审核中）。
- 同一信号换参数生成的新 alpha被系统判定为重复，无法真正提交。

**必须二次确认**：

```python
alpha = session.get(f"{API_BASE}/alphas/{alpha_id}").json()
print(alpha.get("status"))  # ACTIVE 才算真正提交成功

# 如果 status == UNSUBMITTED，查看 checks 中 SELF_CORRELATION 结果
for c in alpha.get("is", {}).get("checks", []):
    print(c["name"], c.get("result"), c.get("value"))
```

**获取全部 alpha 并统计 ACTIVE 数量**：

```python
def get_all_alphas(session, limit=100):
    all_alphas = []
    offset = 0
    while True:
        data = session.get(f"{API_BASE}/users/self/alphas", params={"limit": limit, "offset": offset}).json()
        batch = data.get("results", data.get("alphas", []))
        if not batch:
            break
        all_alphas.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return all_alphas

all_alphas = get_all_alphas(session)
active = [a for a in all_alphas if a.get("status") == "ACTIVE"]
print(f"total={len(all_alphas)}, ACTIVE={len(active)}")
```

---

## 8. 组合构建规则

### 8.1  diversified 组合示例

| 簇 | 代表表达式 |
|----|------------|
| 盈利能力 | `group_rank(ts_rank(operating_income/equity, 126), subindustry)` |
| 分析师 | `group_rank(ts_rank(est_eps/close, 252), subindustry)` |
| FCF | `group_rank(ts_rank(free_cash_flow_reported_value/equity, 126), industry)` |
| 低相关混合 | `0.5*rank(-(close/open-1)) + 0.5*rank(ts_rank(operating_income/equity, 126))` |
| 质量组合 | `0.5*group_rank(ts_rank(oi/equity,126),subindustry) + 0.5*group_rank(ts_rank(est_eps/close,126),industry)` |

### 8.2 提交优先级

1. 高 Fitness（≥ 1.5）且低 TO（< 15%）
2. 来自不同信号簇
3. 若 SELF_CORRELATION 冲突，保留高 Fitness 版本

### 8.3 相关性的真相

对 ACTIVE alpha 的日收益做相关分析，发现：

- **同一信号簇内相关性极高**：
  - 两个 open-close 反转 + OI/Equity 混合（权重不同）日收益相关 **0.84**
  - 两个分析师 EPS 相关 **0.74**
  - 两个杠杆/质量因子（`-equity/assets` vs `liabilities/assets`）相关 **0.84**
- **跨簇也不一定能分散**：基于 `scl12_buzz` 的情绪 alpha 与基于 `est_eps/close` 的分析师 alpha 相关仍达 **0.59–0.67**。
- **累计 PnL 相关性严重失真**： alpha 的累计 PnL 两两相关普遍 **> 0.90**，容易让人误以为所有因子都一样。

**结论**：

- 换窗口、换权重、换 neutralization **不能创造真正的低相关**。
- 真正的低相关来自 **完全不同的数据来源或经济逻辑**（如：宏观事件、期权流、跨境、另类数据）。
- 在常规 USA TOP3000 基本面/价量/分析师池子里，"低相关" 往往是 **0.3–0.6 的日收益相关**，不要追求 0。

---

## 9. 提交前 Checklist

- [ ] 已获取 **所有** alpha 列表（含 ACTIVE / UNSUBMITTED），不只是本次模拟
- [ ] 新因子与已有 ACTIVE alpha **日收益** 相关性 < 0.7（或新 Sharpe ≥ 旧 Sharpe × 1.1）
- [ ] 相关性基于 **日收益** 计算，不是累计 PnL
- [ ] 字段已验证
- [ ] 模拟无报错
- [ ] Sharpe ≥ 1.3（理想 ≥ 1.5）
- [ ] Fitness ≥ 1.1
- [ ] Turnover 1%–20%（可放宽至 ≤ 35%）
- [ ] Drawdown < 15%
- [ ] 所有 IS 检查 PASS
- [ ] 多空数量合理
- [ ] 提交后 **再次确认 status == ACTIVE**，201 不代表上线

---

## 10. 核心经验（一句话版）

1. **先生成因子前先拉取所有 ACTIVE alpha 的 PnL**，避免高相关重复。
2. **相关性必须算日收益**，累计 PnL 相关会把所有因子看成同一个。
3. **201 响应 ≠ 提交成功**：提交后必须确认 `status == ACTIVE`。
4. **基本面 > 混合 > 技术**：`operating_income/equity`、`est_eps/close`、`free_cash_flow_reported_value/equity` 是最稳起点。
5. **group_rank + ts_rank 是黄金组合**。
6. **SUBINDUSTRY 中性化通过率最高**。
7. **Decay 是控制换手的主杠杆**：基本面 0，技术 10–30。
8. **50/50 正交混合能降低换手，但未必能降低相关**；相关靠信号来源，不靠权重。
9. **字段先验证**，无效字段秒级报错。
10. **USA TOP3000 里真正的低相关很难做**；同一数据池的 "不同" 表达式往往高度相关。

---

## 11. 自进化机制

每次与 BRAIN 交互（提交、查询、分析）后，AI 应把新发现写回本 SKILL，使其随实战经验持续进化。

### 11.1 触发条件

以下任一情况发生后，运行一次 `scripts/evolve_skill.py`：

- 提交了一个或多个新 alpha
- 批量回测了一批 alpha
- 查询了 alpha 状态并发现变化（如 UNSUBMITTED → ACTIVE，或被拒绝）
- 发现了新的字段可用性/失效模式

### 11.2 运行方式

**前提**：设置 `WQ_BRAIN_USERNAME` / `WQ_BRAIN_PASSWORD`，或在 skill 目录下放置未跟踪的 `credential.txt`，内容为 BRAIN 账号密码 JSON 数组：

```json
["your_username", "your_password"]
```

```bash
# 1. 预览：生成建议追加的 markdown 片段，不修改任何文件
pyenv exec python scripts/evolve_skill.py

# 2. 提交：追加到 SKILL.md 并更新 alpha_db.json
pyenv exec python scripts/evolve_skill.py --apply
```

> 注意：**不带 `--apply` 的预览模式不会修改 `alpha_db.json` 和 `SKILL.md`**，你可以先审查再提交。脚本仅依赖 `requests` 和 `numpy`，**不需要 `wq-bus` 项目代码**。数据文件已随 SKILL 分发。

脚本会：

1. 拉取 `/users/self/alphas`（分页）获取全部 alpha。
2. 与本地 `alpha_db.json` 对比，找出 **新增** 或 **状态/指标变化** 的 alpha。
3. 对新 alpha 抓取 `recordsets/pnl`，计算与已有 ACTIVE alpha 的 **日收益相关性**。
4. 自动生成经验条目（指标评价 + 相关评价 + 表达式摘要）。
5. 第一次运行输出**批量快照**；后续运行输出**增量条目**。
6. `--apply` 模式下把条目追加到 `## 12. 实证记录（自动更新）`，并保存本地 `alpha_db.json`。

### 11.3 AI 应如何整理经验

脚本输出后，AI 需要**人工判断**哪些条目值得永久写入 SKILL：

- **保留**：高 Fitness 低换手的成功案例、新的低相关信号簇、意外的失败模式。
- **精简**：大量重复的同一信号簇条目应合并为一句话规律。
- **更新模板/阈值**：如果多次发现某个字段/模板失效，应回到第 4、5、6 节更新。

### 11.4 数据结构

- `alpha_db.json`：本地 alpha 快照库，包含状态、指标、表达式、PnL。该文件会包含个人研究记录，默认被 `.gitignore` 忽略，不应提交到公开仓库。
- `SKILL.md`：最终人类可读 playbook，第 12 节只保留脱敏后的通用经验。

## 12. 实证记录（自动更新）

> 本节仅保留机制说明。真实运行生成的 alpha ID、表达式、PnL、提交状态和相关性记录可能关联个人账号与研究资产，默认写入本地 `alpha_db.json`，不随仓库发布。
> 若需要沉淀通用经验，请人工汇总成脱敏规则后再写回第 4、5、6、8、10 节。




### 2026-09-29 09:40 UTC — 批量初始化快照

- 总 alpha：349 | ACTIVE：13 | 非 ACTIVE：336
- 信号簇分布：{'other': 95, 'technical': 94, 'analyst': 78, 'analyst+technical': 55, 'sentiment': 23, 'cashflow': 3, 'quality/leverage': 1}

**ACTIVE 高 Fitness Top 5**：
- `rKOZWNJJ` (analyst): Sharpe=2.73, Fitness=2.42, TO=0.107 — `0.5 * group_rank(ts_rank(operating_income / equity, 126), subindustry) + 0.5 * group_rank(ts_rank(est_eps / close, 12...`
- `E5p9K3gr` (analyst): Sharpe=2.11, Fitness=1.68, TO=0.161 — `group_rank(ts_rank(est_eps / close, 126), industry)`
- `omLZoqXl` (analyst): Sharpe=2.38, Fitness=1.61, TO=0.320 — `-1 * (group_rank(ts_decay_linear((close - open) / open, 4), subindustry) - 0.5) + (group_rank(-ts_corr(est_ptp, est_f...`
- `A1NmeMQY` (analyst): Sharpe=2.06, Fitness=1.42, TO=0.287 — `-1 * (group_rank(ts_decay_linear((close - open) / open, 4), subindustry) - 0.5) + (group_rank(ts_rank(est_eps / close...`
- `d5bNdNVX` (other): Sharpe=2.01, Fitness=1.32, TO=0.063 — `group_rank(ts_rank(operating_income / equity, 126), subindustry)`

**ACTIVE 中日收益高相关对**：无 ≥ 0.7 的对（或 PnL 不足）

**明显失效信号（Fitness < 0.5，共 108 个）**：
- 簇分布：{'technical': 32, 'analyst': 30, 'other': 27, 'sentiment': 9, 'analyst+technical': 8}

**高换手（TO > 50%，共 36 个）**：
- 簇分布：{'technical': 22, 'other': 10, 'analyst': 3, 'analyst+technical': 1}

---


### 2026-09-29 10:25 UTC

- **0mX1j63r** (UNSUBMITTED, cashflow): Sharpe=1.16, Fitness=0.8, TO=0.0338, DD=0.1183。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_decay_linear(free_cash_flow_reported_value / equity, 10), subindustry)`
- **3qX8jGe6** (ACTIVE, analyst): Sharpe=2.47, Fitness=1.24, TO=0.4824, DD=0.0428。换手偏高，需增大 decay 或混合稳定信号；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(est_ptp / close, 126), industry) - 0.5 * group_rank(ts_rank(close / open - 1, 20), subindustry)`
- **YPbRxbXJ** (UNSUBMITTED, other): Sharpe=0.67, Fitness=0.28, TO=0.0529, DD=0.0602。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ebitda / sales, 126), subindustry)`
- **akb5VJXW** (UNSUBMITTED, analyst): Sharpe=2.49, Fitness=1.88, TO=0.1845, DD=0.0402。满足基础提交门槛；与 3qX8jGe6 高度相关 (0.84)，需换信号簇
  - 相关：3qX8jGe6(+0.84)
  - 表达式：`0.5 * group_rank(ts_rank(est_ptp / close, 126), industry) - 0.5 * group_rank(ts_rank(close / open - 1, 20), subindustry)`
- **XgbdJv5m** (UNSUBMITTED, analyst): Sharpe=2.81, Fitness=1.49, TO=0.473, DD=0.0361。换手偏高，需增大 decay 或混合稳定信号；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(est_ptp / close, 126), industry) - 0.5 * group_rank(ts_rank(close / open - 1, 20), subindustry)`

---


### 2026-09-29 10:50 UTC

- **akb5R2R2** (UNSUBMITTED, other): Sharpe=1.1, Fitness=0.52, TO=0.0573, DD=0.0372。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank((sales - cogs) / sales, 126), subindustry)`
- **JjN63MpE** (UNSUBMITTED, analyst): Sharpe=2.21, Fitness=1.53, TO=0.2021, DD=0.0448。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(est_fcf / close, 126), industry) - 0.5 * group_rank(ts_rank(close / open - 1, 20), subindustry)`
- **78NeYnPb** (ACTIVE, other): Sharpe=1.8, Fitness=1.56, TO=0.1151, DD=0.06。高 Fitness 低换手，优秀候选；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(est_sales / close, 126), industry) + 0.5 * group_rank(ts_rank(operating_income / assets, 126...`

---


### 2026-09-29 11:05 UTC

- **QPbwNV5M** (UNSUBMITTED, other): Sharpe=2.27, Fitness=1.82, TO=0.1105, DD=0.0358。高 Fitness 低换手，优秀候选；与 3qX8jGe6 中等相关 (0.59)，谨慎提交
  - 相关：3qX8jGe6(+0.59)
  - 表达式：`0.5 * group_rank(ts_rank(est_ebit / close, 126), industry) + 0.5 * group_rank(ts_rank(operating_income / equity, 126)...`
- **akb5ex2R** (UNSUBMITTED, other): Sharpe=2.74, Fitness=2.46, TO=0.1064, DD=0.0323。高 Fitness 低换手，优秀候选；与 3qX8jGe6 中等相关 (0.54)，谨慎提交
  - 相关：3qX8jGe6(+0.54)
  - 表达式：`0.5 * group_rank(ts_rank(est_ebit / close, 126), industry) + 0.5 * group_rank(ts_rank(operating_income / equity, 126)...`
- **YPbR8kaq** (ACTIVE, analyst): Sharpe=2.13, Fitness=1.52, TO=0.1793, DD=0.0459。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(est_fcf / close, 126), industry) - 0.5 * group_rank(ts_rank(close / open - 1, 20), subindustry)`

---


### 2026-09-29 13:56 UTC

- **rKe1mOzd** (ACTIVE, technical): Sharpe=2.57, Fitness=2.27, TO=0.1474, DD=0.0268。高 Fitness 低换手，优秀候选；与 3qX8jGe6 中等相关 (0.52)，谨慎提交
  - 相关：3qX8jGe6(+0.52)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_advanced_af_nd_epsr_mean, 252) / close, 126), subindust...`
- **P0gJEwbL** (UNSUBMITTED, other): Sharpe=1.09, Fitness=0.51, TO=0.2402, DD=0.0447。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(nws12_mainz_1p), 252), 63), subindustry) - 0.5 * group_rank(ts_rank(ts_d...`
- **omW1eO7b** (UNSUBMITTED, other): Sharpe=1.24, Fitness=0.94, TO=0.1756, DD=0.087。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(est_sales / close, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_linear((close - op...`
- **9qWzQLeK** (ACTIVE, other): Sharpe=1.67, Fitness=1.12, TO=0.157, DD=0.0387。满足基础提交门槛；与 rKe1mOzd 中等相关 (0.52)，谨慎提交
  - 相关：rKe1mOzd(+0.52), 3qX8jGe6(+0.43)
  - 表达式：`-1 * (group_rank(ts_decay_linear((close - open) / (open + 0.0001), 5), subindustry) - 0.5) + (group_rank(ts_rank(ts_b...`
- **gJZYpMjK** (UNSUBMITTED, other): Sharpe=0.25, Fitness=0.06, TO=0.1735, DD=0.0862。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_advanced_af_nd_cff_mean, 252) / (close + 0.0001), 126), subin...`
- **88Pm11RW** (UNSUBMITTED, other): Sharpe=1.68, Fitness=1.3, TO=0.1726, DD=0.0892。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`-1 * (group_rank(ts_decay_linear((close - open) / (open + 0.0001), 5), subindustry) - 0.5) + (group_rank(ts_rank(ts_b...`
- **xAbKgg3n** (UNSUBMITTED, other): Sharpe=0.18, Fitness=0.02, TO=1.3433, DD=0.1195。换手偏高，需增大 decay 或混合稳定信号；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_netprofit_median, 252) / (shareholders_equity_...`
- **RR6JnK3n** (UNSUBMITTED, other): Sharpe=1.08, Fitness=0.51, TO=0.1689, DD=0.0655。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_netprofit_median, 252) / (shareholders_equity_...`
- **1YZqQXeK** (UNSUBMITTED, other): Sharpe=0.8, Fitness=0.41, TO=0.0669, DD=0.074。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_advanced_af_nd_ptpr_median, 252), 126), subindustry) - ...`
- **QPK1JWOg** (UNSUBMITTED, technical): Sharpe=0.92, Fitness=0.38, TO=0.2495, DD=0.0955。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(forward_price_10 / (close + 0.001), 126), subindustry) - 0.5 * group_rank(ts_rank((close - v...`
- **9qWzv6mK** (UNSUBMITTED, technical): Sharpe=1.5, Fitness=1.19, TO=0.178, DD=0.0892。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(fn_def_tax_assets_net_a / (close + 0.001), 126), industry) - 0.5 * group_rank(ts_rank(return...`
- **RR6JMgMn** (UNSUBMITTED, technical): Sharpe=0.86, Fitness=0.44, TO=0.1503, DD=0.1187。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ppe_gross_value / (total_assets_amount + 0.001), 126), subindustry) - 0.5 * group_rank(ts_ra...`
- **j285v6Vo** (UNSUBMITTED, analyst+technical): Sharpe=2.19, Fitness=1.57, TO=0.1664, DD=0.0393。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(est_eps / (close + 0.001), 126), subindustry) - 0.5 * group_rank(ts_rank((close - vwap) / (v...`
- **zqbvZ65K** (UNSUBMITTED, technical): Sharpe=1.13, Fitness=0.57, TO=0.1565, DD=0.074。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(shareholders_equity_max / (capital_expenditure_amount + 0.001), 126), subindustry) - 0.5 * g...`
- **A1vRjQ1w** (UNSUBMITTED, technical): Sharpe=0.96, Fitness=0.43, TO=0.3033, DD=0.1139。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(news_pe_ratio / (close + 0.001), 126), subindustry) - 0.5 * group_rank(ts_rank((close - vwap...`
- **j285zWbW** (UNSUBMITTED, technical): Sharpe=1.26, Fitness=0.67, TO=0.1471, DD=0.0488。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(net_income_median / (max_total_assets_quarterly_estimate + 0.001), 126), subindustry) - 0.5 ...`
- **LLZPLEWm** (UNSUBMITTED, other): Sharpe=0.73, Fitness=0.38, TO=0.1276, DD=0.0602。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(anl4_ebit_mean / close, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_delay(close, 20) / ...`
- **JjN66zbW** (UNSUBMITTED, other): Sharpe=-0.44, Fitness=-0.33, TO=0.1127, DD=0.6697。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`-1 * group_rank(ts_decay_linear(parkinson_volatility_120, 10), subindustry) * ts_rank(gross_income_reported_value / c...`
- **YPbRRb9R** (UNSUBMITTED, technical): Sharpe=1.23, Fitness=0.78, TO=0.1881, DD=0.0905。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`-1 * group_rank(ts_decay_linear((close - vwap) / vwap, 5), subindustry) * ts_rank(parkinson_volatility_120, 66)`
- **gJbnnMm0** (UNSUBMITTED, technical): Sharpe=0.55, Fitness=0.12, TO=0.7968, DD=0.1021。换手偏高，需增大 decay 或混合稳定信号；暂无 ACTIVE alpha 可比相关
  - 表达式：`-1 * group_rank(ts_decay_linear((close - vwap) / vwap, 5), subindustry) * ts_rank(implied_volatility_mean_30, 60)`
- **pwR99zoj** (UNSUBMITTED, technical): Sharpe=0.92, Fitness=0.26, TO=0.7056, DD=0.0973。换手偏高，需增大 decay 或混合稳定信号；暂无 ACTIVE alpha 可比相关
  - 表达式：`-1 * group_rank(ts_decay_linear((close - vwap) / vwap, 5), subindustry) * ts_rank(unsystematic_risk_last_30_days, 63)`
- **LLN00LJv** (UNSUBMITTED, technical): Sharpe=1.79, Fitness=0.66, TO=0.6784, DD=0.0377。换手偏高，需增大 decay 或混合稳定信号；暂无 ACTIVE alpha 可比相关
  - 表达式：`-1 * group_rank(ts_decay_linear((close - vwap) / vwap, 5), subindustry) * ts_rank(snt_buzz_ret, 20)`
- **e7bXjWbg** (UNSUBMITTED, technical): Sharpe=1.73, Fitness=0.91, TO=0.4287, DD=0.0884。换手偏高，需增大 decay 或混合稳定信号；暂无 ACTIVE alpha 可比相关
  - 表达式：`-1 * group_rank(ts_decay_linear((close - vwap) / vwap, 5), subindustry) * ts_rank(parkinson_volatility_120, 66)`
- **JjN69PM2** (UNSUBMITTED, technical): Sharpe=1.01, Fitness=0.34, TO=0.5579, DD=0.0788。换手偏高，需增大 decay 或混合稳定信号；暂无 ACTIVE alpha 可比相关
  - 表达式：`-1 * group_rank(ts_decay_linear((close - vwap) / vwap, 5), subindustry) * ts_rank(book_value_per_share_2 / close, 126)`
- **npdLE2bq** (UNSUBMITTED, other): Sharpe=0.61, Fitness=0.25, TO=0.1521, DD=0.0569。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(anl4_afv4_cfps_median / close, 126), subindustry) - group_rank(ts_rank(implied_volatility_mean_30,...`
- **MPaJZRL6** (UNSUBMITTED, technical): Sharpe=0.61, Fitness=0.23, TO=0.1536, DD=0.0487。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(operating_income / assets, 126), subindustry) - 0.5 * group_rank(ts_decay_linear((volume / a...`
- **gJbnG26v** (UNSUBMITTED, other): Sharpe=0.56, Fitness=0.22, TO=0.0462, DD=0.0635。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(-ts_rank(liabilities / assets, 252), subindustry)`
- **e7bXGNXd** (UNSUBMITTED, other): Sharpe=0.97, Fitness=0.49, TO=0.0465, DD=0.0668。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(-ts_rank(capex / assets, 252), subindustry)`
- **levoGJeO** (UNSUBMITTED, technical): Sharpe=0.45, Fitness=0.11, TO=0.4674, DD=0.1221。换手偏高，需增大 decay 或混合稳定信号；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(abs(close / open - 1) / (close * volume), 60), subindustry)`

---


### 2026-09-29 14:35 UTC

- **P0gJqVKq** (ACTIVE, other): Sharpe=1.62, Fitness=1.1, TO=0.1604, DD=0.0696。满足基础提交门槛；与现有 ACTIVE alpha 低相关 (0.21)，分散价值较高
  - 相关：rKe1mOzd(+0.21), 3qX8jGe6(+0.20), 9qWzQLeK(-0.18)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_afv4_eps_high, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay...`
- **1YZq1Wom** (UNSUBMITTED, other): Sharpe=0.92, Fitness=0.52, TO=0.1503, DD=0.0616。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(vec_avg(nws12_afterhsz_3s), 252), 126), subindustry) - group_rank(ts_rank(ts_decay_lin...`
- **O081q987** (UNSUBMITTED, other): Sharpe=0.95, Fitness=0.4, TO=0.2285, DD=0.0801。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(nws12_prez_maxdnamt), 252), 126), subindustry) - 0.5 * group_rank(ts_ran...`
- **akxrJnzx** (UNSUBMITTED, other): Sharpe=0.76, Fitness=0.46, TO=0.1353, DD=0.0717。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(est_tot_assets / close, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_backfill(vec_avg(nw...`
- **vR2K0qW3** (UNSUBMITTED, technical+sentiment): Sharpe=1.15, Fitness=0.49, TO=0.3359, DD=0.0632。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(scl12_buzz_fast_d1, 20), subindustry) - group_rank(ts_rank(ts_decay_linear(returns, 5), 20), subin...`
- **j285QkPW** (UNSUBMITTED, other): Sharpe=1.37, Fitness=0.91, TO=0.1578, DD=0.0603。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(vec_avg(anl4_qfv4_actual), 252) / close, 126), subindustry) - group_rank(ts_rank(ts_de...`

---


### 2026-09-29 15:09 UTC

- **vR2KelW3** (ACTIVE, technical): Sharpe=1.84, Fitness=1.05, TO=0.1906, DD=0.0469。指标一般，需继续优化；与现有 ACTIVE alpha 低相关 (0.39)，分散价值较高
  - 相关：P0gJqVKq(+0.39), rKe1mOzd(+0.12), 3qX8jGe6(+0.10)
  - 表达式：`group_rank(ts_rank(gross_income_reported_value / est_tot_assets, 126), subindustry) - group_rank(ts_rank(close / vwap...`
- **LLZPgX8m** (UNSUBMITTED, other): Sharpe=-1.07, Fitness=-0.45, TO=0.2956, DD=0.2654。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(vec_avg(fnd6_newqeventv110_pncwiepq), 252), 126), subindustry) - group_rank(ts_rank(im...`
- **QPK12ZL5** (UNSUBMITTED, other): Sharpe=1.76, Fitness=0.83, TO=0.2646, DD=0.0309。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(anl4_adxqfv110_low), 252), 126), subindustry) - 0.5 * group_rank(ts_rank...`
- **gJZYo0PO** (UNSUBMITTED, other): Sharpe=0.48, Fitness=0.18, TO=0.1795, DD=0.066。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fcf_number, 252) / close, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_...`
- **786kK6Zb** (UNSUBMITTED, other): Sharpe=2.51, Fitness=1.65, TO=0.2004, DD=0.0264。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(vec_avg(anl4_fs_guidance_basic_qf_v4_nd_estimate), 252), 126), subindustry) - group_ra...`
- **mL6PxZd5** (UNSUBMITTED, sentiment): Sharpe=-0.57, Fitness=-0.24, TO=0.1931, DD=0.2225。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(scl12_sentiment_fast_d1, 20), subindustry) - group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimat...`
- **kqgxL5z8** (UNSUBMITTED, other): Sharpe=1.33, Fitness=0.84, TO=0.216, DD=0.0584。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(vec_avg(anl4_dez1basicqfv4_est), 252) / close, 126), subindustry) - group_rank(ts_rank...`
- **O081m3m1** (UNSUBMITTED, technical): Sharpe=-0.04, Fitness=-0.01, TO=0.1503, DD=0.2516。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(vec_avg(nws12_mainz_01l), 252), 60), subindustry) - group_rank(ts_rank(ts_decay_linear...`
- **gJZYPz7v** (UNSUBMITTED, other): Sharpe=2.08, Fitness=1.59, TO=0.1695, DD=0.0428。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_ptp_mean, 252) / close, 126), subindustry) - group_r...`
- **xAbKQpoW** (UNSUBMITTED, other): Sharpe=1.13, Fitness=0.69, TO=0.1882, DD=0.074。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_ebit_number, 252) / close, 126), subindustry) - grou...`
- **xAbKQw5n** (UNSUBMITTED, technical): Sharpe=1.8, Fitness=1.45, TO=0.1539, DD=0.0445。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(anl4_qf_az_eps_mean, 252) / close, 126), subindustry) - group_rank(ts_rank(ts_decay_li...`

---


### 2026-09-29 16:16 UTC

- **GrOqG8NG** (ACTIVE, other): Sharpe=1.62, Fitness=1.0, TO=0.1682, DD=0.0501。指标一般，需继续优化；与 P0gJqVKq 中等相关 (0.68)，谨慎提交
  - 相关：P0gJqVKq(+0.68), vR2KelW3(+0.50), 3qX8jGe6(+0.32)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fcf_mean, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_line...`
- **0mrbMNZp** (UNSUBMITTED, analyst): Sharpe=2.06, Fitness=1.48, TO=0.175, DD=0.0452。满足基础提交门槛；与 GrOqG8NG 高度相关 (0.71)，需换信号簇
  - 相关：GrOqG8NG(+0.71), rKe1mOzd(+0.58), 3qX8jGe6(+0.58)
  - 表达式：`group_rank(ts_rank(est_fcf / close, 126), subindustry) - group_rank(ts_rank(ts_decay_linear((close - open) / open, 5)...`
- **kqgxZmOO** (UNSUBMITTED, other): Sharpe=1.84, Fitness=1.56, TO=0.1599, DD=0.0575。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(anl4_netprofita_std, 252) / (ts_backfill(vec_avg(fourteen_period_avg_true_range), 252)...`
- **vR2Kv3Zz** (UNSUBMITTED, technical): Sharpe=1.64, Fitness=1.04, TO=0.2204, DD=0.0642。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(vec_avg(nws12_afterhsz_postvwap), 252) / close, 126), subindustry) - group_rank(ts_ran...`
- **0mrbEZXK** (UNSUBMITTED, other): Sharpe=1.31, Fitness=0.92, TO=0.2028, DD=0.0933。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(anl4_fs_basic_splt_v4_nd_sales_previosestimate), 252) / close, 126), sub...`
- **LLZP1gav** (UNSUBMITTED, analyst): Sharpe=1.74, Fitness=1.24, TO=0.1851, DD=0.0633。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(est_ebitda / close, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_linear((close - o...`
- **d512xoRv** (UNSUBMITTED, technical): Sharpe=2.23, Fitness=1.7, TO=0.1658, DD=0.0392。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_netprofit_median, 252) / close, 126), subindustry) - 0.5 * group_rank(ts_ra...`
- **3qVM7W6X** (UNSUBMITTED, other): Sharpe=2.16, Fitness=1.69, TO=0.1861, DD=0.0471。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_advanced_af_nd_ptp_high, 252) / close, 126), subindustr...`
- **58gZvgbM** (UNSUBMITTED, other): Sharpe=2.07, Fitness=1.46, TO=0.1765, DD=0.0275。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(anl4_fs_actual_1qf_v4_nd_fcf_value, 252) / close, 126), subindustry) - group_rank(ts_r...`
- **E5RZk6JL** (UNSUBMITTED, other): Sharpe=1.64, Fitness=1.0, TO=0.1589, DD=0.0546。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_epsr_value, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_li...`
- **KPrKkdNE** (UNSUBMITTED, other): Sharpe=1.51, Fitness=0.65, TO=0.3349, DD=0.0381。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(vec_avg(scl12_sentvec), 252), 126), subindustry) - group_rank(ts_rank(ts_decay_linear(...`
- **Vk0vXRQ5** (UNSUBMITTED, other): Sharpe=0.6, Fitness=0.22, TO=0.1462, DD=0.0475。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`group_rank(ts_rank(ts_backfill(anl4_qf_az_eps_mean, 252) / close, 126), subindustry) - group_rank(ts_rank(implied_vol...`
- **2rm1vjgx** (UNSUBMITTED, other): Sharpe=0.06, Fitness=0.01, TO=0.2957, DD=0.1341。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(nws12_afterhsz_5s), 252), 126), subindustry) - 0.5 * group_rank(ts_rank(...`
- **E5RZqwx0** (UNSUBMITTED, other): Sharpe=0.68, Fitness=0.4, TO=0.0916, DD=0.0869。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(anl4_fs_basic_splt_v4_nd_sales_previosestimate), 252) / close, 126), sub...`

---


### 2026-09-29 16:19 UTC

- **N1VXbVbo** (ACTIVE, technical): Sharpe=2.7, Fitness=2.04, TO=0.1811, DD=0.0302。满足基础提交门槛；与 3qX8jGe6 中等相关 (0.68)，谨慎提交
  - 相关：3qX8jGe6(+0.68), rKe1mOzd(+0.61), 9qWzQLeK(+0.43)
  - 表达式：`group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_epsr_high, 252) / close, 126), subindustry) - group_...`

---


### 2026-09-29 17:40 UTC

- **O0816Lr7** (ACTIVE, technical): Sharpe=1.9, Fitness=1.31, TO=0.1573, DD=0.0682。满足基础提交门槛；与 P0gJqVKq 高度相关 (0.76)，需换信号簇
  - 相关：P0gJqVKq(+0.76), GrOqG8NG(+0.69), vR2KelW3(+0.45)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_ptp_value, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_lin...`
- **d5129VWJ** (UNSUBMITTED, technical): Sharpe=0.41, Fitness=0.13, TO=0.1484, DD=0.0688。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_ptp_mean, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(volume / ts_b...`
- **QPK108Mw** (UNSUBMITTED, technical): Sharpe=-0.06, Fitness=-0.01, TO=0.1545, DD=0.1301。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_basic_qf_v4_nd_sales_median, 252), 126), subindustry) -...`
- **A1vRx5Me** (UNSUBMITTED, technical): Sharpe=1.39, Fitness=0.73, TO=0.1752, DD=0.0371。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(nws12_prez_3l), 252), 126), subindustry) - 0.5 * group_rank(ts_rank(retu...`
- **N1VXPN2L** (UNSUBMITTED, technical): Sharpe=1.5, Fitness=0.9, TO=0.1637, DD=0.0449。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_cfi_mean, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_line...`
- **RR6JenMz** (UNSUBMITTED, technical): Sharpe=0.05, Fitness=0.01, TO=0.1488, DD=0.0845。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_ebitda_mean, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_l...`
- **O081AVdg** (UNSUBMITTED, sentiment): Sharpe=1.35, Fitness=0.6, TO=0.2856, DD=0.0326。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(scl12_sentiment_fast_d1, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_linear((clos...`
- **xAbKrw7w** (UNSUBMITTED, technical): Sharpe=1.55, Fitness=1.01, TO=0.1587, DD=0.0685。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_netprofit_median, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_de...`
- **j285Yv85** (UNSUBMITTED, other): Sharpe=1.02, Fitness=0.62, TO=0.1242, DD=0.087。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_epsr_mean, 252), 126), subindustry) - 0.5 * gr...`
- **blOY8g8R** (UNSUBMITTED, technical): Sharpe=1.37, Fitness=0.79, TO=0.1649, DD=0.0406。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_cff_mean, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_line...`
- **XgJjRaE0** (UNSUBMITTED, other): Sharpe=0.28, Fitness=0.1, TO=0.1706, DD=0.185。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_guidances_advanced_af_nd_fcfps_minguidance, 252), 126), subindustry) - 0...`
- **zqbvrRb1** (UNSUBMITTED, technical+sentiment): Sharpe=0.97, Fitness=0.38, TO=0.3119, DD=0.051。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(earnings_evaluation_sentiment), 252), 126), subindustry) - 0.5 * group_r...`
- **QPK1YrP5** (UNSUBMITTED, technical): Sharpe=-0.06, Fitness=-0.01, TO=0.1662, DD=0.1624。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_advanced_af_nd_fcfps_median, 252), 126), subindustry) -...`
- **d512KpOK** (UNSUBMITTED, technical): Sharpe=1.28, Fitness=0.76, TO=0.1541, DD=0.0727。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fcf_value, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_lin...`
- **6XKnK6a7** (UNSUBMITTED, technical): Sharpe=1.06, Fitness=0.48, TO=0.2152, DD=0.0459。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(nws12_prez_result2), 252), 126), subindustry) - 0.5 * group_rank(ts_rank...`
- **gJZYZXnJ** (UNSUBMITTED, technical): Sharpe=-0.21, Fitness=-0.05, TO=0.1585, DD=0.1041。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_ebit_median, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_l...`
- **88PmPlnV** (UNSUBMITTED, technical): Sharpe=1.55, Fitness=1.0, TO=0.1474, DD=0.0424。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_netprofita_number, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_d...`
- **E5RZRGj9** (UNSUBMITTED, technical): Sharpe=1.31, Fitness=0.73, TO=0.1635, DD=0.0516。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_advanced_af_nd_cfo_mean, 252), 126), subindustry) - 0.5...`
- **2rm1wgdP** (UNSUBMITTED, technical): Sharpe=1.28, Fitness=0.6, TO=0.2086, DD=0.0432。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(anl4_dez1basicqfv4_preest), 252), 126), subindustry) - 0.5 * group_rank(...`
- **blOYbJaq** (UNSUBMITTED, technical): Sharpe=1.31, Fitness=0.83, TO=0.15, DD=0.0719。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_qfv4_median_eps, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_dec...`
- **2rm1wl5P** (UNSUBMITTED, technical): Sharpe=1.55, Fitness=0.97, TO=0.1562, DD=0.0586。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_ebit_low, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_line...`
- **pw5qPLzq** (UNSUBMITTED, technical): Sharpe=1.71, Fitness=1.12, TO=0.1606, DD=0.058。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_advanced_af_nd_fcf_median, 252), 126), subindustry) - 0...`
- **RR6JV2nn** (UNSUBMITTED, technical): Sharpe=1.65, Fitness=1.06, TO=0.1609, DD=0.0531。指标一般，需继续优化；与 GrOqG8NG 高度相关 (0.81)，需换信号簇
  - 相关：GrOqG8NG(+0.81), O0816Lr7(+0.74), P0gJqVKq(+0.66)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_cfo_mean, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_decay_line...`
- **GrOql9X0** (UNSUBMITTED, technical): Sharpe=1.69, Fitness=1.14, TO=0.1541, DD=0.0462。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_median_epsreported, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_...`
- **XgJj7Rgm** (UNSUBMITTED, technical): Sharpe=1.08, Fitness=0.62, TO=0.1521, DD=0.0799。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_ebitda_mean, 252), 126), subindustry) - 0.5 * ...`
- **GrOqlw3x** (UNSUBMITTED, other): Sharpe=1.2, Fitness=0.7, TO=0.1648, DD=0.0609。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_gric_mean, 252), 126), subindustry) - 0.5 * group_rank(ts_rank(ts_delta(clo...`
- **XgJj7wnz** (UNSUBMITTED, sentiment): Sharpe=0.17, Fitness=0.03, TO=0.2642, DD=0.0622。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(vec_avg(merger_acquisition_sentiment), 252), 126), subindustry) - 0.5 * group_ra...`
- **786kzRLZ** (UNSUBMITTED, other): Sharpe=1.47, Fitness=0.87, TO=0.1563, DD=0.0264。指标一般，需继续优化；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_capex_number, 252), 126), subindustry) - 0.5 *...`

---


### 2026-09-29 18:25 UTC

- **2rm1ePwb** (ACTIVE, technical): Sharpe=1.76, Fitness=1.02, TO=0.166, DD=0.0347。指标一般，需继续优化；与 O0816Lr7 中等相关 (0.56)，谨慎提交
  - 相关：O0816Lr7(+0.56), GrOqG8NG(+0.54), P0gJqVKq(+0.45)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_advanced_af_nd_sh_equity_std, 252), 126), subindustry) ...`
- **omW1radn** (UNSUBMITTED, technical): Sharpe=2.45, Fitness=1.89, TO=0.1729, DD=0.032。满足基础提交门槛；与 N1VXbVbo 高度相关 (0.80)，需换信号簇
  - 相关：N1VXbVbo(+0.80), 3qX8jGe6(+0.65), O0816Lr7(+0.62)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_epsr_low, 252) / close, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_de...`
- **P0gJ8qlM** (UNSUBMITTED, technical): Sharpe=1.99, Fitness=1.34, TO=0.1794, DD=0.0403。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_netprofit_mean, 252) / close, 126), subindustr...`
- **GrOqxJL3** (UNSUBMITTED, other): Sharpe=1.7, Fitness=1.02, TO=0.1669, DD=0.034。指标一般，需继续优化；与 N1VXbVbo 高度相关 (0.74)，需换信号簇
  - 相关：N1VXbVbo(+0.74), 3qX8jGe6(+0.55), rKe1mOzd(+0.53)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_ptp_mean, 252) / close, 126), subindustry) - 0...`
- **blOYz9Kp** (UNSUBMITTED, other): Sharpe=2.3, Fitness=1.68, TO=0.183, DD=0.0405。满足基础提交门槛；与 3qX8jGe6 高度相关 (0.78)，需换信号簇
  - 相关：3qX8jGe6(+0.78), N1VXbVbo(+0.77), rKe1mOzd(+0.70)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_netprofit_mean, 252) / close, 126), subindustr...`
- **3qVMkKge** (UNSUBMITTED, technical): Sharpe=2.51, Fitness=2.05, TO=0.189, DD=0.0402。满足基础提交门槛；与 rKe1mOzd 高度相关 (0.76)，需换信号簇
  - 相关：rKe1mOzd(+0.76), 3qX8jGe6(+0.67), 9qWzQLeK(+0.62)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimates_advanced_af_nd_netprofit_mean, 252) / close, 126), subi...`
- **zqbv25Rd** (UNSUBMITTED, technical): Sharpe=1.91, Fitness=1.34, TO=0.1537, DD=0.0432。满足基础提交门槛；与 O0816Lr7 高度相关 (0.72)，需换信号簇
  - 相关：O0816Lr7(+0.72), GrOqG8NG(+0.70), 2rm1ePwb(+0.63)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_netprofita_std, 252), 126), subindustry) - 0.5...`
- **leKLENbn** (UNSUBMITTED, technical): Sharpe=2.27, Fitness=1.71, TO=0.1792, DD=0.0453。满足基础提交门槛；与 3qX8jGe6 高度相关 (0.77)，需换信号簇
  - 相关：3qX8jGe6(+0.77), N1VXbVbo(+0.76), rKe1mOzd(+0.67)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_ptp_mean, 252) / close, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_de...`
- **88PmV7rz** (UNSUBMITTED, other): Sharpe=1.76, Fitness=1.23, TO=0.17, DD=0.0533。满足基础提交门槛；与 rKe1mOzd 高度相关 (0.73)，需换信号簇
  - 相关：rKe1mOzd(+0.73), 3qX8jGe6(+0.66), N1VXbVbo(+0.66)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_ebit_high, 252) / close, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_d...`

---


### 2026-09-29 18:40 UTC

- **QPK1o3Ng** (ACTIVE, technical): Sharpe=2.19, Fitness=1.75, TO=0.1803, DD=0.0391。满足基础提交门槛；与 N1VXbVbo 中等相关 (0.70)，谨慎提交
  - 相关：N1VXbVbo(+0.70), 3qX8jGe6(+0.62), rKe1mOzd(+0.50)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_actuals_advanced_af_nd_ptp_value, 252) / close, 126), subindustry) - 0.5...`
- **88Pm2mwm** (UNSUBMITTED, technical): Sharpe=2.0, Fitness=1.41, TO=0.178, DD=0.0447。满足基础提交门槛；与 GrOqG8NG 高度相关 (0.79)，需换信号簇
  - 相关：GrOqG8NG(+0.79), QPK1o3Ng(+0.69), N1VXbVbo(+0.63)
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_fcf_mean, 252) / close, 126), subindustry) - 0...`
- **3qVMJedN** (UNSUBMITTED, technical): Sharpe=2.48, Fitness=1.95, TO=0.1742, DD=0.0347。满足基础提交门槛；暂无 ACTIVE alpha 可比相关
  - 表达式：`0.5 * group_rank(ts_rank(ts_backfill(anl4_epsr_mean, 252) / close, 126), subindustry) - 0.5 * group_rank(ts_rank(ts_d...`

---

