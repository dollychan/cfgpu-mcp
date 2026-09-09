# request_id 定锚的可恢复提交 — 实现设计

> 与 [`sync-model-to-async.md`](sync-model-to-async.md) 的关系：那篇解的是**吞吐**问题
> （同步模型占住 worker），把后台执行做成 `deferred` 开关，默认关闭。本篇解的是**可恢复性**
> 问题（钱花了、痕迹没了），结论是可恢复性**不能是开关**——一个默认关闭的 durability 等于
> 没有。本篇取代 sync-model-to-async.md 的 §5.1 与 §14 Phase 1；其 §6（token 加密）在本篇
> 的决策下**不再需要**（见 D7），§7（reconciler）保留骨架但职责大幅收窄。

**评审记录（2026-09-09）**：D3 租户键本期不做；D7 收窄为「只维护状态、不自动重试」；
`upstream_task_id` 降为纯内部字段，agent 侧不再感知；deerflow 侧 `_pin_request_id` 确认
升为所有 generate_* 的**必带**参数，且钉的值从 `tool_call_id` 改为 host 生成的 uuid
（D10）。以下正文已按此定稿。

**实现记录（2026-09-09，Phase 1 已落地于 `feat/request-id-durability`）**：新增两条决策——D11
（schema 迁移改由 alembic 承担）与 D12（`tasks` 增 `model_used` 列，供按模型统计生成时延），
以及 §7.1 里「提交失败按它证明了什么分流」这条实现期补充。其余与本设计一致。

---

## 1. 背景

### 1.1 两个诉求

1. **MCP 收到的所有任务都落在 task_manager 里，用 `request_id` 作为 id**。
   generate_* 被中断或 cancel 之后，凭 `request_id` 依然能找回状态。
2. **同步模型的运行状态要被保护住**，使 MCP 能处理两侧的异常：
   - MCP 自己中断的 sync 请求 → 状态可判定；
   - agent 侧中断的请求 → MCP 继续把这个 sync 跑完并落库，agent 事后用 `request_id` 查结果。

### 1.2 现状：四条已核实的事实

**(a) `request_id` 已经到达，但埋在 JSON 里。**
`tools/generate.py` 的三个 generate_* 都接 `request_id`，`task_manager._stash_internal()`
把它塞进 stored payload 的 `_request_id` 键（`task_manager.py:79`）。它没有列、没有索引、
没有任何按它查询的入口。

**(b) 同步模型在 POST 之前，DB 里没有任何行。**

```python
# task_manager.py:531 — create() 的同步分支
resp = await self._client_for(adapter).post(adapter.endpoint, payload)   # ← 10–60s，计费在这里发生
result = adapter.parse_response(resp)
...
await self._repo.insert_task(task_id, adapter.adapter_id, "succeeded", stored_payload)  # ← 行在这之后才出现
```

任何落在第 1 行和第 5 行之间的中断，结果都是**已计费、零痕迹**：

| 中断来源 | 机制 | 结果 |
|---|---|---|
| MCP 侧（systemd restart / OOM / 部署） | 进程消失 | 上游照跑照计费，本机无行 |
| agent 侧（cancel / 断连 / host 超时） | ASGI 请求任务被取消 → `await post()` 抛 `CancelledError` | 同上 |

`errors.py:106` 的 `outcome_unknown` 标志就是给这个状态写的注释——「上游可能已接受并计费，
本服务无法判断」。**本设计的目标就是让这个状态不再存在。**

**(c) 异步模型只覆盖了一半。**
异步分支在 POST 返回后立即写 `pending` 行，所以行存在、可跨实例续查。但那个 task_id
**只存在于那条响应里**——响应丢了（BUG-115 的现网场景：SSE 流随 MCP 重启干净 EOF），
task_id 就永久丢失。行在 DB 里，agent 拿不到钥匙。

**(d) 唯一在调用发出前就已知的句柄，是 `request_id`。**
deerflow 侧 `HumanApprovalMiddleware._pin_request_id`（`human_approval_middleware.py:402`）
把它钉进 `args`，注释写明 "Always overrides any model/user-supplied value"。host 在
**发出调用之前**就知道这个值，所以响应丢失不影响它。这是它能当恢复锚点的全部理由，也是
本设计选它做主键的全部理由。

> 钉的**值**当前是 `tool_call_id`，将改为 host 生成的 uuid（D10）。这不影响上面这条性质
> ——「调用发出前已知」来自钉的**时机**，与钉什么值无关。

---

## 2. 目标与非目标

### 2.1 目标

| # | 目标 |
|---|---|
| G1 | 任何可能已经计费的提交，DB 里一定有一条行 |
| G2 | 该行可凭 `request_id` 直接查到，无需 task_id |
| G3 | agent 侧中断不影响 sync 请求跑完并落库 |
| G4 | 中断之后，agent 能得到**准确**的信息：这次提交究竟发出去了没有 |
| G5 | 同一个 `request_id` 的重复投递不产生第二次计费 |
| G6 | 现有 task_id 全部继续可解析 |

### 2.2 非目标

- **MCP 不自动重试**（评审定案）。它只维护状态、把准确信息交给 agent，由 agent 决定下一步。
  这条否掉了 sync-model-to-async.md §7 的「回收即重新派发」，连带否掉它的 §6（token 加密
  持久化）——重新派发才需要别的实例拿到原调用方 token，不重新派发就不需要存 token。
- **不做通用幂等**。`request_id` 一次调用一个（D10），模型**重新发起**一次工具调用会拿到
  一个新的，因此 G5 覆盖「同一次调用被重复投递」（checkpoint 重放、host 重发），
  **不覆盖模型自己决定重试**。见 §10。
- **本期不做租户隔离**（评审定案，见 D3）。
- **不改 `is_async` 语义**。它继续只描述上游 API 行为。
- **不引入外部队列。**

---

## 3. 不变量

| # | 不变量 |
|---|---|
| **I1** | 任务行在上游 POST **之前**写入。没有例外，同步异步一致。 |
| **I2** | 调用方给了 `request_id` 时，它**就是**行主键。不存在第二个 agent 需要记住的 id。 |
| **I3** | 主键冲突不是错误，是去重信号：返回既有行，不再发第二次 POST。 |
| **I4** | 状态词汇能区分「POST 确定未发出」和「POST 可能已发出」。 |
| **I5** | 已计费的写回不在调用方的 cancel 作用域内。 |
| **I6** | `upstream_task_id` 只服务 MCP↔上游 的轮询，**不出现在任何对 agent 的返回里**。 |
| **I7** | 存量行（`id` = 上游 task_id、`upstream_task_id` 为 NULL）继续可查、可轮询。 |

---

## 4. 核心决策

### D1 — `id` 就是 `request_id`

调用方给了 `request_id`，它直接做行主键；没给（CLI / dispatcher / openai_tools 等不带该
参数的调用方）才回落到本机 uuid——即今天同步模型的行为。

这是诉求 1 的字面实现，也让整个设计塌缩掉一层：不需要额外的 `request_id` 列，不需要它的
索引，不需要「按 request_id 查」这条独立查询路径，不需要唯一约束——**主键就是全部**。

对外返回的 `task_id`，对带 request_id 的调用就等于 request_id。结果里同时还有
`stamp_echo` 打上的 `request_id` 字段（既有行为，不动），两者同值，互为佐证。

### D2 — 主键冲突 = 去重，返回既有行

`INSERT ... ON CONFLICT DO NOTHING`；没插进去就回查那一行并返回它，**不抛错、不发 POST**。

对**非终态**行：这是「上一次还在跑」，返回它让调用方接着 wait/poll。
对**终态**行：这是「上一次已经跑完」，返回既有结果——**不重新生成、不重新计费**。

后者是刻意的。同一个 `request_id` 第二次到达，只可能是重放（checkpoint replay、host 重发），
而不是一次新的创作意图；模型真想重来会发起新的工具调用，那会带一个新的 `request_id`。

**这条完全依赖 D10 的持久化条件**：重放读回的必须是同一个 `request_id`。一旦 host 在执行
时现算（而不是钉进 AIMessage），每次重放都是新值，本决策静默失效——症状是双倍计费，
没有报错、没有日志。

> **与 BUG-021 的关系（精确表述）**：这条**能**关掉「同一条 AIMessage 被重放导致重复计费」
> ——`aget_state.next` 续跑路径重放的是同一条 AIMessage，其 `args.request_id` 是烘死的，
> 撞主键即命中缓存。它**关不掉**「stale run 从 START 整轮重算」——那条路径重新调 LLM，
> 会生成新的 tool_call，`request_id` 随之是新的，MCP 侧无从判别。不要把这条当成 BUG-021
> 的完整修复。

### D3 — 本期不做租户键

评审定案：MCP 侧只按 request_id 响应结果，不引入 `tenant_key`。

需要记在案的一点，供日后决策，**不在本期动**：

- 今天 `task_status(task_id)` **不做任何租户校验**，任何 token 都能查任何 task_id。这是
  既有缺口，本设计不扩大也不修复它——D1 之后 id 仍然是一个 uuid（D10），不可枚举，
  可猜测性与今天没有实质变化。

> 初稿在这里还记了第二条风险：「两个租户的 tool_call_id 撞号会让后者拿到前者的任务」。
> D10 把 `request_id` 从 provider 生成的 tool_call_id 换成 host 生成的 uuid 之后，这条
> **不再存在**——撞号需要两个独立 uuid4 相同。这也是 D10 比补租户键更划算的地方。

### D4 — 两个新的非终态状态，把「是否已发出」变成可判定的

| 状态 | 含义 | 对 agent 意味着什么 |
|---|---|---|
| `submitting` | 行已落，POST **确定未发出** | 上游没有计费，重发是安全的 |
| `dispatching` | POST 已派发或其响应已丢失，**无法证明未发出** | 上游可能已计费，**不要直接重发** |
| `pending` / `running` | 异步，上游 id 已知 | 不需要做什么，轮询即可 |
| `succeeded` / `failed` | 终态 | — |

在「MCP 不自动重试」定案之后，这两个状态的用途从**闸门**变成了**信息本身**：它们就是 G4
所说的「准确的信息」。没有这条分界线，中断后能说的只有「不知道」。

代价是同步路径多一次 UPDATE（`submitting` → `dispatching`，紧贴 POST 之前）。这条路径被
一次 10–60s 的上游调用支配，多一次本地写可忽略。

两个新状态是 cfgpu-mcp 自己的词汇，**不进 `_STATUS_MAP`**——那张表映射上游状态，上游永远
不会说这两个词。

### D5 — 已计费的写回不进 cancel 作用域

POST + parse + 写回结果整块放进 `asyncio.shield`。agent 断连时外层 `await` 立刻抛
`CancelledError`（该结束的请求正常结束），被 shield 的协程继续跑到写库为止（I5）。

这是 G3 的最小实现：**不需要后台 worker，不需要 reconciler，不需要 token 加密**，且
`wait=True` 的现有行为逐字节不变。sync-model-to-async.md 的后台 worker 能力更强（顺带让
`wait=False` 对同步模型有意义），但它解决不了 shield 解决不了的任何一个中断来源——
**进程死亡两者都救不了**，那是 §7.3 sweeper 的职责。因此后台 worker 归入 Phase 3（吞吐
诉求），不进本期。

### D6 — `upstream_task_id` 降为纯内部字段

评审定案：agent 侧不需要知道 upstream_task_id，agent↔MCP 之间只有 request_id。

它的存在理由只剩一条：`poll()` 要用它拼上游的 poll URL。所以：

- 新增列 `upstream_task_id`，异步模型在 POST 返回后写入。
- `poll()` 用 `upstream_task_id or id` 拼 URL。`or id` 是给存量行的：老行的 `id` **就是**
  上游 id，该列为 NULL 时自动落回，行为与今天一致（I7）。
- 它**不出现在** `to_dict()` / `pending_result()` / `_present()` 的任何返回里（I6）。
- 查询解析顺序：`id` → 未命中则 `upstream_task_id`。第二跳只为存量与兼容存在，走索引，
  只在未命中时发生。

### D7 — MCP 只维护状态，不自动重试

**评审定案，取代原设计的「`submitting` 超时自动重新派发」。**

sweeper（Phase 2）扫到卡住的非终态行时，只做一件事：**把它收敛到一个准确的终态并说清楚
原因**，绝不重新发 POST。

| 行卡在 | sweeper 动作 |
|---|---|
| `submitting` | 标 `failed`，`error_type="submission_lost"`，文案：**上游未收到，重发是安全的** |
| `dispatching` | 标 `failed`，`error_type="submission_lost"`，文案：**上游可能已计费，不要直接重发** |
| `pending` / `running`（异步） | 不动。现有 `task_status` 的 re-poll 已经覆盖 |

这条决策连锁地删掉了三样东西：`token_enc`（重新派发才需要别的实例拿到原 token）、
`attempt` / `max_retries`（不重试就无所谓次数）、以及跨实例 reclaim 的全部并发控制
（`FOR UPDATE SKIP LOCKED` 只在多个实例抢着重新派发时才需要；只做状态收敛的话，两个实例
把同一行标成同一个终态是幂等的）。**Phase 2 因此从「回收器」缩成「清道夫」。**

### D8 — 错误文案按「照着做能不能成」写

终态文案必须给出**当前工具面真的做得到**的下一步。有了 D1 之后，这样的下一步才第一次存在：

- `submitting` 丢失 → 「这次提交没有发出，上游未执行也未计费。可以用相同参数重新发起。」
- `dispatching` 丢失 → 「上游可能已经执行并计费，但结果没能取回。先用
  `task_status("<request_id>")` 确认，**不要直接重发同一个任务**。」

（在此之前这两句都写不出来：没有 `list_tasks`，没有 task_id，模型唯一能执行的动作是重新
提交。这正是 BUG-105 / BUG-111 那条判据说的循环——「错误文案是按照着做能不能成来打分的」。）

### D9 — `outcome_unknown` 的适用面收窄

`errors.py:106` 现在的定义是「submit POST 请求期超时，没有 task_id，**也没有写过任务行**，
所以既无可轮询也无可对账」。I1 落地后行一定存在，该注释描述的情形不再可达。改为只在
「行写入本身失败」这一残留缺口上置位，注释同步改写。

### D10 — `request_id` 是 host 生成的 uuid，与 `tool_call_id` 解耦

**评审定案。** 今天 `_pin_request_id` 钉的值是 `tc["id"]`，即 provider 生成的 tool_call_id。
D1 之后这个值**就是共享 PG 的主键**，跨全部租户、全部 thread，于是 provider 的 id 生成策略
变成了本设计的正确性前提——而它不是任何契约的一部分：OpenAI 给 `call_` + 24 位十六进制，
Anthropic 给 `toolu_` + base62，本地模型 / vLLM / 某些兼容层会发 `call_1`、`0`、
`chatcmpl-tool-...` 这类按消息序号走的短 id。

撞号的后果不是报错，是 D2 的**去重命中**：B 的提交拿到 A 的任务，返回 A 的产物。所以钉的值
改为 host 自己生成的 uuid4，与 tool_call_id 无关。这比补 `tenant_key`（D3）便宜得多，也不
依赖任何上游行为。

#### 唯一的硬条件：只生成一次，并烘进 AIMessage

D2 的去重（连同它关掉的那半个 BUG-021）依赖「同一次调用重复投递时 `request_id` 不变」。
uuid 满足这一点的前提是**它被持久化**，而不是每次执行现算。

当前钉点恰好满足：`_build_response` 在 `human_approval_middleware.py:470` 钉，
:527 `new_msg = ai_msg.model_copy(update={"tool_calls": new_tool_calls})` →
:540 `{"messages": [new_msg, ...]}` 落进 checkpoint；`model_copy` 保 id，
`add_messages` 走替换。所以 uuid 被烘进 AIMessage，**checkpoint 重放读回的是同一个值**。

因此 §9.1 那条「钉点上移到所有 generate_* 出口」的改动带一条**硬约束**：新钉点必须同样是
写 AIMessage 的钩子（`after_model` 一族），**不得落到 `wrap_tool_call` 等执行期位置**。
违反的症状是双倍计费，且没有报错、没有日志。

> 若将来确实需要一个执行期可用的钉点，替代方案是
> `uuid5(NAMESPACE, f"{thread_id}:{message_id}:{tool_call_id}")`——纯函数，不依赖持久化，
> 顺带保住可追溯性。只要钉点留在 `after_model`，uuid4 更简单，不必上这套。

#### 代价：跨层可追溯性

今天一个 `call_xxx` 能在 LLM transcript、审批卡片、MCP task 表、cfgpu 日志之间直接 grep 串
起来；解耦后 transcript 那一段断了。补法是钉的时候记一行
`INFO: pinned request_id=<uuid> for tool_call_id=<tc_id>`，那一行就是 join 表。

### D11 — schema 迁移交给 alembic

**实现期定案。** 本设计是这张表的第一次 schema 变更，此前的做法是每次连接跑
`CREATE TABLE IF NOT EXISTS` 再补 `ALTER TABLE ... ADD COLUMN`。那套只对「纯增列」成立：
没有历史、没有 down、没有次序，下一次要删列或改类型时无处落脚，而共享 PG 上「代码升了、
库没升」这个窗口没有任何东西挡着。

- **alembic 是所有持久化存储 schema 的唯一权威**；仓库 `connect()` 在开库/建池**之前**
  `await ensure_schema(url)`，因此不存在代码比库超前一版的时刻。
- **在连接期跑，而不是做成部署步骤**——沿用这个服务本来的行为（实例由 systemd 各自重启，
  没有可以挂手工 upgrade 的协调点）。并发由 `migrations/env.py` 里的
  `pg_advisory_xact_lock` 串行化，取在 alembic 自己的事务里，与旧代码同一把 key。
- **迁移环境两种方言都是异步的**（`sqlite+aiosqlite` / `postgresql+asyncpg`），因为本项目
  只声明了这两个驱动；`ensure_schema` 于是把 alembic 放在工作线程里跑。
- **每条 revision 先查再做**：所有现存部署都已经有表却没有 `alembic_version` 行，
  guard 让 `upgrade head` 直接纳管，不需要 stamp、也不需要运维判断它在哪一版。
- **`:memory:` 不走 alembic**——每条连接就是一个独立的库。它由 `client/db.py` 保留的 DDL
  直接建在 head 上，两份描述的漂移由
  `test_migrations.py::test_migrated_schema_matches_the_in_memory_bootstrap` 逐列比对钉住。

代价是两个新的核心依赖（alembic + SQLAlchemy）。放进核心而不是 extras：每种部署形态启动时
都要开这个库，一个落后一版的 schema 是在第一次查询时炸，不是在某个可选时刻。

### D12 — `model_used` 列（报表用）

**用户提出，实现期采纳。** 与可恢复性无关，搭这次迁移的车。

它是 `adapter_id` 的冗余投影——两者由注册表一一对应，所以这一列**不带新信息**。仍然值得加：
`updated_at - created_at` 在终态行上现在才是**真实的端到端时延**（I1 之前 `created_at` 写在
POST 之后，这个差值只覆盖写库那一瞬），而按模型分组统计它不应该要求报表侧去 join 一份 YAML
注册表，也不该让内部 `adapter_id` 泄漏进报表口径。存的是公开的 `model_name`。

两条使用注意，写在这里免得报表跑出错误结论：**只统计终态行**（`updated_at` 会被每次轮询推
进），以及**异步模型的差值含排队时间**，不是纯生成耗时。

---

## 5. 数据模型变更

### 5.1 新增列（`client/task_row.py::COLUMNS`）

| 列 | 类型 | 说明 |
|---|---|---|
| `upstream_task_id` | TEXT NULL | 异步模型的上游 id。**纯内部**（D6/I6） |
| `model_used` | TEXT NULL | 公开模型名，写在 insert 时。纯报表用途，运行期不读（D12） |

**可恢复性只需要第一列。** D1（id 即 request_id）省掉了 `request_id` 列及其索引与唯一约束；
D3 省掉了 `tenant_key`；D7 省掉了 `token_enc` 与 `attempt`。第二列与本设计的目标无关，
搭这次迁移的车（D12）。

`request_id` 在 stored payload 里的 `_request_id`（`_stash_internal`）**保持不变**——
`_present()` 的 echo 路径读的就是它，不动它就等于那条路径零改动。

### 5.2 索引

```sql
CREATE INDEX IF NOT EXISTS idx_tasks_upstream ON tasks(upstream_task_id);
```

### 5.3 迁移

**由 alembic 承担（D11）**，两条 revision：`0001_initial_tasks` 是既有表的基线，
`0002_task_recovery_columns` 加这两列与 `idx_tasks_upstream`。纯增列，无改列无删列。

**存量行**：`upstream_task_id` 为 NULL → `poll()` 落回 `id`，行为不变（I7）。**不回填。**

---

## 6. 状态机

```
                         ┌──────────────┐
   create() 落行 ───────►│  submitting  │  POST 确定未发出 → 重发安全
                         └──────┬───────┘
                                │ UPDATE（紧贴 POST 之前）
                         ┌──────▼───────┐
                         │ dispatching  │  无法证明未发出 → 不要直接重发
                         └──┬────────┬──┘
             is_async=false │        │ is_async=true
                            │        │  （写入 upstream_task_id）
              ┌─────────────▼─┐   ┌──▼──────────┐
              │  succeeded    │   │   pending   │──► running ──►┐
              │  / failed     │   └─────────────┘               │
              └───────────────┘        （现有轮询链路不变）      ▼
                                                          succeeded / failed
```

需要同步跟进的判据点——每一处漏掉都会把新状态误读成别的东西：

| 位置 | 现状 | 需要的改动 |
|---|---|---|
| `_TERMINAL_STATUSES` | `{succeeded, failed}` | 不变（两个新状态都非终态） |
| `list_running_tasks()` | `status IN ('pending','running')` | 加入两个新状态，否则 sweeper 看不见孤儿 |
| `service/task.py::needs_repoll` | `in ("pending","running")` | `dispatching` 不 re-poll——那时还没有 upstream_task_id |
| `TaskManager.wait()` | `while status not in _TERMINAL` | 天然覆盖；`dispatching` 期间无上游 id，须等它出现再 poll |
| `pending_result()` | 原样回显 status | 代码不改，但工具 docstring 要解释这两个状态 |

---

## 7. 流程

### 7.1 `create()`

```python
async def create(self, adapter, req):
    request_id = getattr(req, "request_id", None)
    task_id = request_id or str(uuid.uuid4())          # D1

    payload = adapter.build_payload(req)
    stored = _stash_internal(payload, req, aspect_ratio=adapter.is_async)

    # I1：这行落地之后，任何中断都留下痕迹
    inserted = await self._repo.insert_task(
        task_id, adapter.adapter_id, "submitting", stored
    )
    if not inserted:                                    # D2：主键冲突 = 去重
        existing = await self._repo.get_task(task_id)
        logger.info("request_id=%s 已存在（status=%s），返回既有任务，不重复提交",
                    task_id, existing["status"])
        return Task(existing)

    # I4：越过这条线之后，「POST 从未发出」不再可证
    await self._repo.update_task(task_id, "dispatching")

    # I5：已计费的结果写回不在调用方的 cancel 作用域内
    return await asyncio.shield(self._dispatch(task_id, adapter, req, payload))
```

`_dispatch()` 就是今天 `create()` 里 POST 之后的全部逻辑，唯一区别是终点从 `insert_task`
换成 `update_task`——行已经在了：

- 同步：`post → parse → 产物守卫 → update(succeeded, result)`
  - 产物守卫不通过：上游明确答了且明确没产物，是**已知终态**，`raise` 之前先
    `update(failed, error=…)`，不留给清道夫去猜。
- 异步：`post → extract_task_id → update(pending, upstream_task_id=...)`
  - 解析不出上游 id：今天是直接 `raise`。现在 `raise` **之前**先
    `update(failed, error="submission_lost …")`——这正是「已计费、句柄丢失」那个格子。

**提交失败按它证明了什么分流**（`_record_submit_failure`，实现期补充）。POST 抛错时行不能
一律留在 `dispatching`：那等于对每一次 4xx 都宣布「可能已计费、别重发」，而 4xx 恰恰证明了
相反的事。判据是**上游有没有答话**：

| 失败 | 证明了什么 | 行 |
|---|---|---|
| 4xx / 内容审核 / 配额 / 鉴权 / 模型不可用 | 上游答了并拒收，没执行没计费 | `failed`（照实说，可修可重发） |
| connect 阶段超时 | DNS/TCP/TLS 没走完，一个字节都没出去 | `failed` |
| request 阶段 POST 超时 / 传输层错误 / 上游 5xx | **证明不了任何事** | 留 `dispatching`，清掉 `outcome_unknown`，文案补上 `task_status("<request_id>")` |

不对称是刻意的：把「拒收」标成 `dispatching` 只是让人多查一次；把「不确定」标成 `failed` 会
让人放心重发一个正在跑的任务。

### 7.2 agent 侧中断（BUG-115 的现网场景）

```
agent                   MCP 实例                     上游
  │  generate_image      │                            │
  │  request_id=call_x   │                            │
  ├─────────────────────►│ insert(id=call_x, submitting)
  │                      │ update(dispatching)        │
  │                      │ POST ──────────────────────►│  ← 计费
  │   ✗ 断连/cancel      │                            │
  │◄─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┤ CancelledError（外层）      │
  │                      │ ┌── shield 内继续 ────┐    │
  │                      │ │ parse ◄─────────────┼────┤
  │                      │ │ update(succeeded)   │    │
  │                      │ └─────────────────────┘    │
  │                                                   │
  │  task_status("call_x")                            │
  ├─────────────────────►│ → succeeded + urls         │
```

**钱花了，产物拿得到。** 这是当前实现拿不到的。注意 agent 用的是它**发出调用前就已经知道**
的 `call_x`，全程不依赖任何一条可能丢失的响应。

### 7.3 MCP 侧中断（systemd restart）

进程消失时 shield 也一起消失。行留在 DB 里，由 sweeper 收敛（Phase 2，D7）：

| 行停在 | 结论 | 交给 agent 的话 |
|---|---|---|
| `submitting` | POST 从未发出 | 重发是安全的 |
| `dispatching` | 无法判定 | 上游可能已计费，先查，别直接重发 |
| `pending`/`running` | 上游 id 已知 | 不用做什么，下次 `task_status` 自然推进 |

在 sweeper 落地之前（Phase 1 结束时），这两类行会停在非终态直到有人查它。**这仍然严格优于
今天**——今天连行都没有。

---

## 8. 工具面变更

### 8.1 不加参数，改 docstring

因为 `id` 就是 `request_id`（D1），`task_status(task_id)` / `task_wait(task_id)` 的现有签名
**已经**能承载恢复路径，不需要新增 `request_id` 参数。加一个纯同义参数只会扩大 schema
表面而不增加任何能力。

要改的是 docstring，让模型在恢复时刻知道该传什么：

> `task_id` — generate_* 返回的 id。**如果那次调用带了 `request_id`，两者是同一个值**：
> 上一次 generate_* 没有返回结果（超时／连接中断／服务重启）时，把那次的 `request_id`
> 传进来就能查到它的最终状态。

同时要在 docstring 里解释 `submitting` / `dispatching` 两个新状态对调用方分别意味着什么
（D4 表格那两句话）。

> **备选**（未采纳）：额外加一个 `request_id` 形参做别名。否决理由是它不增加能力，而工具
> schema 每多一个参数就多一处模型可以填错的地方。

### 8.2 是否新增 `list_tasks`

**不加。** 有了 D1，「知道自己发过什么却查不到」这个场景已经关闭：request_id 在调用前就
已知，永远不会丢。`list_tasks` 打开的是「列出别人的任务」这个面，而本期不做租户键（D3），
两者叠加不安全。判据留在案上：真的出现「连 request_id 都丢了」的现网案例再议。

---

## 9. 与 agent 侧（deerflow）的接缝

### 9.1 `_pin_request_id` 升为 generate_* 必带 ✅，且钉 uuid

**评审定案（两件事，一起做）。**

**(a) 覆盖面。** 今天它只在 `_build_response`（审批通过路径）和审批卡片构造处生效，未走审批的
generate_*（`_needs_approval` 为假或被 `_excluded`）不带 request_id，那些调用会退回到
uuid 主键，也就退出了本设计的恢复能力。要让恢复覆盖全部 generate_*，deerflow 侧需要把钉的
位置上移到所有 generate_* 出口。

**(b) 钉的值。** 从 `tc["id"]` 改为 host 生成的 uuid4（D10）。

**这两件事一起带一条硬约束**：新钉点必须仍然是写 AIMessage 的钩子（`after_model` 一族），
使钉进 `args` 的 uuid 随 AIMessage 进 checkpoint。**不得把钉点放到 `wrap_tool_call` 等
执行期位置**——那样每次重放都会现算一个新 uuid，D2 的去重静默失效，症状是双倍计费而没有
任何报错或日志。这是本设计对 host 侧唯一的强制要求。

对应地需要一条 host 侧回归测试：**同一条 AIMessage 重放两次，`args["request_id"]` 逐字节
相同**。

**以上都是 host 侧改动，与 MCP 侧 Phase 1 成对上线。** MCP 侧仍按 `request_id` 可能为 None
编写（CLI / dispatcher / openai_tools 本来就不带），不依赖 host 一定钉上，也不关心它长什么
样——对 MCP 而言 `request_id` 只是一个不透明字符串。

### 9.2 host 超时文案现在有救了

BUG-115 的修复在 host 侧给 http/sse MCP 调用包了 900s deadline，超时抛 `ToolException`，
文案是 "Query its status before submitting the same job again"。对 generate_* 这句话此前
**是死路**——task_id 从没回来过，也没有按 request_id 查的入口。

本设计落地后，host 的超时包装可以从 `args["request_id"]`（host 自己钉的，一定知道）合成
一条真能执行的文案：

> 「调用 `cfdream_task_status("<request_id>")` 查这次提交的最终状态。不要重新提交。」

这条 host 侧改动与 MCP 侧 Phase 1 **必须成对上线**，否则文案又会指向一个不存在的入口。

### 9.3 审批卡片仍会转圈（不在本设计范围）

MCP 侧改什么都关不掉那张卡：`MessageStreamMiddleware.awrap_tool_call` 不 catch 异常，而
`terminal_error_tools` 那条通道读的是 payload 里的 `error` 字段。host 侧的独立问题，已记在
BUG-115 的未闭合面里。

---

## 10. 已知限制

| 限制 | 影响 | 说明 |
|---|---|---|
| `dispatching` 崩溃不可恢复 | 上游已计费，结果丢失 | D7 定案：不猜、不重试，只如实上报。窗口从今天的「整个 POST 时长 10–60s」缩到「POST 返回后到 update 之间」（毫秒级） |
| `request_id` 对模型重试不稳定 | 模型自己重发 = 新的一次工具调用 = 新 `request_id` = 新提交 = 二次计费 | §2.2。要根治得靠上游幂等键（当前不提供）或 host 侧策略 |
| 无租户隔离 | 任何 token 可查任何 id | D3 定案本期不做，**已知并接受**。撞号那半边已由 D10 消除 |
| 去重依赖 host 侧持久化 | host 若在执行期现算 uuid，D2 静默失效 → 双倍计费 | D10 的硬条件；靠 §9.1 的重放回归测试守住 |
| 跨层追溯需要一次 join | transcript 里的 tool_call_id 与 task 表里的 request_id 不再同值 | D10 的代价；钉点记一行 INFO 即为 join 表 |
| Phase 1 无 sweeper | 崩溃留下的非终态行不会自我收敛 | 仍优于今天（今天连行都没有）；Phase 2 补 |
| SQLite 单实例 | stdio/CLI 部署只有进程内 shield | 符合预期，那些部署没有多实例 |

---

## 11. 测试策略

| 测试 | 覆盖 |
|---|---|
| `test_row_exists_before_upstream_post` | I1：mock client 在 POST 内断言行已存在且状态为 `dispatching` |
| `test_task_id_is_request_id_when_supplied` | D1 |
| `test_task_id_falls_back_to_uuid_without_request_id` | D1 回落 + §9.1 不依赖 host |
| `test_cancelled_request_still_writes_result` | I5：外层 `task.cancel()` 后仍能查到 `succeeded` |
| `test_duplicate_request_id_returns_existing_live_task` | D2 非终态半边：第二次 create 不调 `client.post` |
| `test_duplicate_request_id_returns_cached_terminal_result` | D2 终态半边：返回既有产物，不重新计费 |
| `test_upstream_task_id_never_surfaces_to_caller` | I6：扫 `to_dict` / `pending_result` / `_present` 的输出 |
| `test_legacy_row_polls_by_id` | I7：`upstream_task_id IS NULL` 的老行仍能 poll |
| `test_lookup_falls_back_to_upstream_task_id` | D6 第二跳 |
| `test_dispatching_is_not_repolled` | §6 判据表：没有上游 id 时不去 poll |
| `test_async_missing_upstream_id_marks_row_failed` | §7.1 异步分支：raise 之前先落终态 |
| `test_sweeper_marks_submitting_safe_to_resend`（Phase 2） | D7 上半 + D8 文案 |
| `test_sweeper_marks_dispatching_do_not_resend`（Phase 2） | D7 下半 + D8 文案 |

集成测试（`CFGPU_RUN_INTEGRATION=1`，真计费）只补一条：
`test_cancelled_generate_recoverable_by_request_id`。**尚未落地。**

**实际落地**（Phase 1，共 +33 条）：`tests/unit/test_request_id_durability.py` 19 条覆盖上表
除两条 Phase 2 之外的全部，另加四条实现期新增的判据——提交被拒收收敛成 `failed`、
不确定的提交留在 `dispatching` 且文案给出可执行下一步、connect 阶段超时判为可安全重发、
`wait()` 撞上未派发行时重读行而不是轮询上游；`tests/unit/test_migrations.py` 10 条覆盖 D11
（从零建库 / 纳管无 `alembic_version` 的存量库且不动存量行 / 幂等 / **与 `:memory:` 引导 DDL
逐列比对** / DSN 翻译 / `:memory:` 不迁移）；`tests/unit/test_postgres_repo.py` 补 4 条
（冲突返回 False、按上游 id 回查、COALESCE 不擦除、`list_running` 含两个新状态）。

---

## 12. 实施阶段

**Phase 1 — 可恢复性地基（✅ 已完成，`feat/request-id-durability`）**
D1 id 即 request_id / D2 冲突即去重 / D4 两个新状态 / D5 shield / D6 `upstream_task_id`
新列与内部化 / D8 文案 / D9 注释收窄 / §6 判据表五处 / §8.1 docstring / 单测。
落地后 G1 G2 G3 G5 G6 达成，G4 达成一半（判据存在，还没有清道夫去收敛崩溃遗留的行）。

**Phase 2 — 清道夫**
周期扫描非终态行，超时的按 D7 收敛到终态并写入 D8 的文案。无 token、无重试、无并发控制
（幂等写）。lifespan 启停。落地后 G4 完整达成。

**Phase 3 — 吞吐（sync-model-to-async.md 的原目标，不在本篇）**
后台 worker，让 `wait=False` 对同步模型真正有意义。地基已由 Phase 1/2 铺好。

**配套（host 侧，另开）**
§9.1 钉点上移 + 钉值改 uuid（D10）+ 重放一致性回归测试 + §9.2 超时文案合成——与 Phase 1
成对上线。三者之中 D10 的硬条件（钉点必须写 AIMessage）是唯一会静默失效的一条，评审时优先
看它。

---

## 13. 否决的替代方案

| 方案 | 否决理由 |
|---|---|
| 只加 `request_id` 列，不动 POST 前后顺序 | 同步模型的行仍在 POST 之后才出现，索引查不到一条不存在的行。**这是唯一必须改的地方** |
| 独立 `request_id` 列 + 部分唯一索引（初稿方案） | D1 之后是纯冗余：主键已经是它，再加一列一索引一约束只是把同一件事说两遍 |
| 主键冲突时抛错 | 把一次「重复投递」变成一次失败。返回既有行才是 G5 想要的 |
| 终态行不去重、允许同 request_id 重新提交 | 那正是 checkpoint 重放导致二次计费的口子；同一个 `request_id` 第二次到达不是新的创作意图 |
| POST 之后改写主键为上游 id | 共享 PG 多实例并发读下改主键代价远高于加一列；改写窗口内该行不可查 |
| 把 `upstream_task_id` 也回给 agent | agent 多记一个 id 却不增加任何能力，而它是**响应到达后**才存在的——恢复场景下正好没有 |
| 用后台 worker 代替 shield 做 Phase 1 | worker 解决不了 shield 解决不了的任何中断来源，进程死亡两者都要清道夫；先上 worker 还会一并改掉 `wait=True` 的现有行为 |
| sweeper 自动重新派发 `submitting` | 评审定案 D7：MCP 不猜。且一旦要重新派发就得跨实例拿原 token，凭空引入 token 加密存储与并发认领 |
| Phase 1 加 `list_tasks` | request_id 调用前已知、永不丢失，它的必要性大幅下降；而在无租户键（D3）时它打开的是「列出别人的任务」 |
| 给 `task_status` 加 `request_id` 形参做别名 | 不增加能力，只增加模型填错的地方；docstring 一句话即可 |
| 继续用 `tool_call_id` 当 `request_id` | 它的唯一性是 provider 的实现细节而非契约（短 id / 序号 id 都存在），而 D1 之后它是共享 PG 的主键；撞号的后果是 D2 去重命中，即跨租户返回别人的产物 |
| 用 `uuid5(thread_id:message_id:tool_call_id)` 代替 uuid4 | 纯函数、执行期可算、保住可追溯性，但当前钉点在 `after_model` 已经持久化，uuid4 足够且更简单。留作「钉点不得不下移」时的后备 |
| 提交失败一律把行留在 `dispatching` | 对每一次 4xx 都宣布「可能已计费」，而 4xx 恰恰证明了相反的事；照实分流只多写一个判据函数 |
| 继续用启动期 `CREATE TABLE IF NOT EXISTS` + `ADD COLUMN` 做迁移 | 只对纯增列成立，没有历史/次序/down；下一次删列或改类型时无处落脚（D11） |
| 让 alembic 也管 `:memory:` | 每条连接就是一个独立的库，迁移会建出一个立刻被丢掉的库；改用逐列比对的测试来防漂移 |
| 在 MCP 侧给 `request_id` 加格式校验（如必须是 uuid） | 对 MCP 而言它只是不透明主键；加校验会把 CLI / dispatcher 等合法调用方挡在外面，且把 host 的实现选择固化进跨仓契约 |
