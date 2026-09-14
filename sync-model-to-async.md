# 同步模型异步化 — 实现设计

## 1. 背景与问题

### 1.1 当前同步模型的执行方式

cfgpu-mcp 的模型按上游 API 契约分为两类：

| `is_async` | 上游 API 行为 | 示例模型 |
|---|---|---|
| `true` | POST 立即返回 task_id，结果需轮询 `poll_endpoint` | wan-2-0, gpt-image-2, seed-tts-2-0 |
| `false` | POST 响应体直接包含结果，无需轮询 | doubao-seedream-5-0-lite, qwen-3-6-plus, minimax-speech-2-8-hd |

`TaskManager.create()` 对两类模型走完全不同的路径（`task_manager.py:148`）：

```
异步模型:  POST → extract_task_id → insert_task("pending") → 返回 Task(pending)
                                   → wait() 循环 poll_endpoint → update_task → succeeded

同步模型:  POST → parse_response → insert_task("succeeded") → 返回 Task(succeeded)
                                   → wait() 直接 return task (no-op)
```

同步模型的 POST 在请求线程内阻塞执行，直到上游返回结果（图片生成 10–60s，TTS 数秒）。

### 1.2 多实例部署下的问题

在多实例 streamable-http + 共享 Postgres 部署中，同步模型有三个结构性缺陷：

1. **请求占住 worker**：一个 `generate_image(model="seedream")` 请求打到实例 A，A 的这个 worker 被占住 10–60s，期间不能服务别的请求。吞吐 = 实例数 × 每实例 worker 并发，而不是任务级的自由调度。

2. **实例挂了无恢复路径**：异步模型在 DB 有 `pending` 行，别的实例可续查（`task_status` re-poll 上游）。同步模型在 `create()` 返回前没有 DB 行，实例中途挂了 = 上游已生成但结果丢失，客户端只能重试（浪费 credits）。

3. **`wait=False` 对同步模型无意义**：`create()` 已经包含了完整结果，`wait=False` 返回的是 `{task_id, status: "succeeded"}`，没有"先提交后查"的能力。

### 1.3 目标场景

用户明确要解决的问题：多个 streamable-http 实例共享一个 Postgres task DB，需要同步模型也能：

- 请求快速返回（不阻塞在上游 POST 上）
- 跨实例恢复（实例挂了，别的实例能接手）
- `wait=False` 真正有效（返回 pending task_id，客户端稍后查结果）

---

## 2. 设计目标

1. **同步模型支持 `wait=False`**：`create()` 写 `pending` 行、立即返回 task_id；上游 POST 在后台执行；完成后写 `succeeded`。
2. **跨实例恢复**：后台 worker 挂了，别的实例的 reconciler 能安全接手。
3. **最小侵入**：不改变异步模型的现有流程；不改变 `service/` 层的调用签名；不改变 `task_status` / `task_wait` 的客户端契约。
4. **opt-in**：默认行为不变（同步模型仍同步执行），通过 config.yaml 开启。

---

## 3. 核心概念：上游异步 vs 执行异步

引入一个与 `is_async` 正交的概念：

| 概念 | 含义 | 来源 |
|---|---|---|
| `is_async`（现有） | 上游 API 是否返回 task_id 需轮询 | adapter.yaml |
| **deferred**（新增） | cfgpu-mcp 是否将上游 POST 延迟到后台执行 | config.yaml |

`is_async` 描述上游行为，**不可配置**。`deferred` 描述 cfgpu-mcp 的执行策略，**可配置**。

组合关系：

| `is_async` | `deferred` | 行为 |
|---|---|---|
| `true` | `false`（默认） | 现有异步流程（POST → pending → poll） |
| `true` | `true` | 不适用（异步模型 POST 本身就秒级返回，无需 deferred） |
| `false` | `false`（默认） | 现有同步流程（POST 阻塞 → succeeded） |
| `false` | `true` | **本设计**：POST 延迟到后台 → pending → succeeded |

**deferred 只对 `is_async: false` 的模型生效。** 对 `is_async: true` 的模型设 deferred 无意义（它们的 POST 已经是秒级返回）。

---

## 4. 整体架构

```
                        ┌─────────────────────────────────────┐
                        │         streamable-http 实例         │
                        │                                     │
  HTTP 请求 ──────────► │  RequestContextMiddleware           │
  (Authorization)       │    │ set_request_token(ContextVar)  │
                        │    ▼                                 │
                        │  tools/generate.py                  │
                        │    │ 透传参数，无逻辑                 │
                        │    ▼                                 │
                        │  service/image.py (generate_image)  │
                        │    │                                 │
                        │    ▼                                 │
                        │  TaskManager.create()                │
                        │    │                                 │
                        │    ├── is_async=true  → 现有异步路径  │
                        │    │                                 │
                        │    └── is_async=false, deferred=true  │
                        │        │ 1. build_payload(req)       │
                        │        │ 2. insert_task("pending")   │
                        │        │    + 存 encrypted_token      │
                        │        │ 3. asyncio.create_task(      │
                        │        │      _run_sync_worker(...))  │
                        │        │ 4. return Task(pending) ◄── 立即返回
                        │        │                               │
                        │        ▼ (后台 event loop)             │
                        │  _run_sync_worker                    │
                        │    │ 5. update_task("running")       │
                        │    │ 6. client.post(endpoint,payload)│
                        │    │ 7. parse_response(resp)         │
                        │    │ 8. update_task("succeeded",...)  │
                        │    └── (异常) update_task("failed")   │
                        │                                     │
                        │  Reconciler (周期扫描)                │
                        │    │ SELECT ... FOR UPDATE SKIP      │
                        │    │   LOCKED WHERE status='pending' │
                        │    │   AND age > threshold            │
                        │    │ → 重新 _run_sync_worker          │
                        │    │                                  │
                        │  task_status / task_wait             │
                        │    │ 读 DB → 若 pending/running 则    │
                        │    │ 轮询 DB (非上游) 直到终态         │
                        └─────────────┬───────────────────────┘
                                      │
                                      ▼
                              ┌──────────────┐
                              │  PostgreSQL  │
                              │  (shared)   │
                              └──────────────┘
```

---

## 5. 详细流程

### 5.1 提交阶段：`TaskManager.create()`（deferred 同步模型）

```python
# task_manager.py — create() 新增 deferred 分支

async def create(self, adapter, req):
    payload = adapter.build_payload(req)

    if adapter.is_async:
        # --- 现有异步路径，不变 ---
        ...
        return Task(_now_row(cfgpu_task_id, ..., "pending", stored_payload))

    # --- 同步模型 ---
    if not self._deferred:
        # 现有同步路径：POST 阻塞 → 立即 succeeded
        resp = await self._client.post(adapter.endpoint, payload)
        result = adapter.parse_response(resp)
        ...
        await self._repo.insert_task(task_id, adapter.adapter_id, "succeeded", payload)
        await self._repo.update_task(task_id, "succeeded", result=result_dict)
        return Task(_now_row(task_id, ..., "succeeded", payload, result=result_dict))

    # --- deferred 同步模型（新增）---
    task_id = str(uuid.uuid4())
    stored_payload = {**payload, _ASPECT_RATIO_KEY: getattr(req, "aspect_ratio", None)}

    # 持久化 token（见 §6）
    token = get_request_token()
    await self._repo.insert_task(
        task_id, adapter.adapter_id, "pending", stored_payload,
        token_enc=_encrypt_token(token) if token else None,
    )

    # 后台 worker（见 §5.2）
    asyncio.create_task(self._run_sync_worker(task_id, adapter, payload, token))

    return Task(_now_row(task_id, adapter.adapter_id, "pending", stored_payload))
```

**关键点**：
- `task_id` 是 cfgpu-mcp 生成的 UUID，不是上游返回的（同步模型上游不返回 task_id）。
- payload 已存入 DB，worker 崩溃后可从 DB 读取重试。
- `asyncio.create_task()` 在当前 event loop 上创建后台任务；ContextVar 在 `create_task` 时被快照复制，所以 worker 能读到 token——即使请求已结束、`reset_request_token` 已调用。

### 5.2 后台执行：`_run_sync_worker()`

```python
async def _run_sync_worker(self, task_id, adapter, payload, token):
    """后台执行同步模型的上游 POST + parse。

    在 create() 的 event loop 上运行；token 从 ContextVar 快照获取。
    任何异常都写入 failed 状态，不向上传播（后台任务无人 catch）。
    """
    try:
        # 标记 running：表示 POST 已派发（reconciler 据此判断是否可安全重试）
        await self._repo.update_task(task_id, "running")

        # 用调用者的 token 发 POST
        resp = await self._client.post_with_token(
            adapter.endpoint, payload, token=token,
        )

        result = adapter.parse_response(resp)
        if not result.model_used:
            result.model_used = adapter.cfgpu_model_id
        if not result.aspect_ratio:
            result.aspect_ratio = payload.get(_ASPECT_RATIO_KEY)
        result_dict = result.to_dict(return_metadata=True)

        if not result_dict.get("urls"):
            # 同 async 路径的 "succeeded but no urls = failure" 守卫
            await self._repo.update_task(task_id, "failed",
                error="Task reported success but returned no artifact URLs")
            return

        await self._repo.update_task(task_id, "succeeded", result=result_dict)

    except Exception as e:
        error_msg = str(e) if not isinstance(e, CFGPUError) else e.user_message
        await self._repo.update_task(task_id, "failed", error=error_msg)
```

**为什么需要 `running` 状态**：

`pending` → POST 已派发但 worker 可能尚未标记 running → **可安全重试**（POST 从未发出）
`running` → POST 已发出 → **不可安全重试**（重发会重复生成，浪费 credits）

reconciler 只回收 `pending` 任务，`running` 超时直接标记 `failed`。

### 5.3 查询阶段：`service/task.py`

#### `get_status()`（task_status）

当前代码对 `pending`/`running` 的异步任务会做一次上游 re-poll（`service/task.py:77-92`）。deferred 同步模型**没有 `poll_endpoint`**，不能 re-poll 上游。只需读 DB：

```python
# service/task.py — get_status() 调整

needs_repoll = task.status in ("pending", "running")
if needs_repoll:
    adapter = registry.get(task.adapter_id)
    if adapter.is_async:
        # 现有逻辑：re-poll 上游
        task = await tm.poll(task, adapter)
    # deferred 同步模型：不 re-poll 上游，直接返回 DB 当前状态
    # （后台 worker 会在自己的时间里更新 DB）
```

#### `wait_for_task()`（task_wait）

当前 `wait()` 对同步模型是 no-op（`task_manager.py:230`）。deferred 同步模型需要**轮询 DB**（非上游）直到终态：

```python
# task_manager.py — wait() 调整

async def wait(self, task, adapter, req, timeout=None, progress_callback=None):
    if not adapter.is_async and not self._deferred:
        return task  # 传统同步模型：no-op

    # 异步模型：轮询上游（现有逻辑）
    # deferred 同步模型：轮询 DB
    effective_timeout = timeout or self._estimate_deferred_timeout(adapter, req)
    interval = 2.0   # DB 轮询间隔（比上游轮询快，因为只是本地读）
    ...

    while task.status not in _TERMINAL_STATUSES:
        await asyncio.sleep(interval)
        ...
        task = await self.status(task.id)  # 从 DB 重读
        ...

    if task.status == "failed":
        raise CFGPUError(...)
    return task
```

### 5.4 `generate_image(wait=True)` 的行为变化

`service/image.py` 当前流程：`create()` → `wait()`。对 deferred 同步模型：

- `create()` 立即返回 `Task(pending)`
- `wait()` 轮询 DB 直到 `succeeded`
- 最终返回结果

从调用方角度看，`wait=True` 的最终结果不变（拿到了 urls），只是内部不再阻塞在上游 POST 而是阻塞在 DB 轮询上。但 HTTP 请求仍然占住连接——这和异步模型的 `wait=True` 一样（`wait()` 也阻塞在上游轮询上）。

**真正受益的是 `wait=False`**：

```python
# service/image.py — generate_image()

task = await tm.create(adapter, req)  # 立即返回 pending

if not wait:
    return {"task_id": task.id, "status": task.status}  # status="pending" ✓
```

客户端稍后调 `task_wait(task_id)` 获取结果。

---

## 6. Token 持久化

### 6.1 问题

后台 worker 需要调用者的 token 来发上游 POST。在进程内 worker 场景，token 通过 ContextVar 快照获取（`asyncio.create_task` 复制当前 context）。但跨实例 reclaim 时，别的实例没有原始调用者的 token。

### 6.2 方案：加密存储

在 `tasks` 表新增 `token_enc` 列，存储用部署密钥加密的 token：

```sql
ALTER TABLE tasks ADD COLUMN token_enc TEXT;  -- AES-encrypted, nullable
```

- **加密密钥**：环境变量 `CFGPU_TASK_TOKEN_KEY`（32 字节，base64 编码）。缺失时 deferred 功能拒绝启动（不静默降级到明文）。
- **加密算法**：AES-256-GCM（`cryptography` 库，已在依赖树中）。每条记录独立 nonce，nonce 随密文一起存储。
- **TTL**：token 在任务终态后由 reconciler 清空（`UPDATE ... SET token_enc = NULL WHERE status IN ('succeeded', 'failed')`），减少密文暴露窗口。
- **明文 token 永不进入日志**：`_run_sync_worker` 的异常处理只记录 error message，不记录 token 或 payload 中的敏感字段。

### 6.3 进程内 worker vs 跨实例 reclaim 的 token 来源

| 场景 | token 来源 | 说明 |
|---|---|---|
| 进程内 worker（正常路径） | ContextVar 快照 | `create_task` 复制 context，worker 直接读 |
| 跨实例 reclaim | DB `token_enc` 解密 | reconciler 解密后注入 ContextVar，再调 worker |

进程内 worker 优先用 ContextVar（避免解密开销）；只有在 reclaim 路径才读 `token_enc`。

### 6.4 替代方案（不采纳）

| 方案 | 否决理由 |
|---|---|
| 不存 token，reclaim 用 `CFGPU_API_TOKEN` 环境变量 | 多租户场景下 token 不通用，reclaim 会用错 token 调上游 |
| 不存 token，不跨实例 reclaim | 退化为 Phase 1，不解决用户的核心诉求 |
| 存明文 token | 安全风险，DB 备份/转储泄漏 |

---

## 7. 孤儿任务回收 (Reconciler)

### 7.1 问题

进程内 `asyncio.Task` 在以下情况会丢失：
- 进程被 kill / OOM / 崩溃
- uvicorn graceful shutdown 时取消未完成的 task
- worker 协程抛出未捕获异常（已在 `_run_sync_worker` 中兜底，但不排除进程级故障）

### 7.2 Reconciler 设计

每个实例运行一个后台循环，周期扫描 `list_running_tasks()`：

```python
async def _reconciler_loop(self):
    """周期扫描 pending/running 任务，回收孤儿。"""
    while True:
        await asyncio.sleep(self._reconcile_interval)  # 默认 30s
        try:
            await self._reconcile_once()
        except Exception:
            logger.exception("Reconciler sweep failed")

async def _reconcile_once(self):
    rows = await self._repo.list_running_tasks()
    now = time.time()
    for row in rows:
        age = now - row["created_at"]
        if row["status"] == "pending" and age > self._pending_timeout:
            # pending 超时 → POST 从未发出 → 安全重试
            await self._reclaim_task(row)
        elif row["status"] == "running" and age > self._running_timeout:
            # running 超时 → POST 已发出但结果未写回 → 不安全重试
            await self._repo.update_task(
                row["id"], "failed",
                error="Worker died after dispatching upstream request",
            )
```

### 7.3 跨实例并发安全

多个实例同时跑 reconciler 会争抢同一批孤儿任务。用 Postgres 的 `SELECT FOR UPDATE SKIP LOCKED` 保证每个任务只被一个实例回收：

```python
# postgres_repo.py — 新增方法

async def claim_stale_pending(self, timeout_seconds: int) -> list[dict]:
    """原子地认领超时的 pending 任务（FOR UPDATE SKIP LOCKED）。

    只返回 pending 任务（running 不在此回收，只标 failed）。
    调用方负责重新执行 _run_sync_worker。
    """
    async with self._pool.acquire() as con:
        async with con.transaction():
            rows = await con.fetch(
                """
                SELECT * FROM tasks
                WHERE status = 'pending'
                  AND created_at < $1
                ORDER BY created_at
                LIMIT 10
                FOR UPDATE SKIP LOCKED
                """
            , now - timeout_seconds)
            if rows:
                ids = [r["id"] for r in rows]
                # 先标记为 running，防止其他实例重复认领
                await con.execute(
                    "UPDATE tasks SET status='running', updated_at=$1 WHERE id = ANY($2)",
                    now, ids,
                )
            return [_row_to_dict(r) for r in rows]
```

SQLite（单实例）不需要 `SKIP LOCKED`（只有一个 reconciler），直接 `SELECT + UPDATE` 即可。

### 7.4 超时阈值

| 参数 | 默认值 | 含义 |
|---|---|---|
| `reconcile_interval` | 30s | 扫描周期 |
| `pending_timeout` | 60s | pending 超过此时间 → 回收重试 |
| `running_timeout` | 300s | running 超过此时间 → 标记 failed |

`pending_timeout` 设为 60s 是因为：正常 worker 从 `create()` 到 `update_task("running")` 只有一个 `asyncio.create_task` 调度延迟（毫秒级）。如果 60s 后还是 pending，几乎可以确定 worker 没跑起来或进程挂了。

`running_timeout` 设为 300s（5 分钟）是因为同步图片生成通常 10–60s，给足余量。可按 task_type 调整（video 更长，但当前同步模型不含 video）。

### 7.5 重试次数限制

回收的 pending 任务重新执行 `_run_sync_worker`，但如果反复失败（比如 token 过期、模型不可用），不应无限重试。在 `tasks` 表新增 `retry_count` 列：

```sql
ALTER TABLE tasks ADD COLUMN retry_count INTEGER DEFAULT 0;
```

reconciler 每回收一次 `retry_count += 1`，超过 `max_retries`（默认 3）后标记 `failed`。

---

## 8. 数据模型变更

### 8.1 新增列

```python
# client/task_row.py — COLUMNS

COLUMNS = (
    "id", "adapter_id", "status", "payload", "result", "error",
    "created_at", "updated_at",
    "token_enc",        # NEW: AES-256-GCM 加密的调用者 token (nullable)
    "retry_count",      # NEW: reconciler 回收次数 (default 0)
)
```

### 8.2 DDL（Postgres + SQLite）

```sql
-- Postgres (postgres_repo.py)
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    adapter_id   TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload      TEXT NOT NULL,
    result       TEXT,
    error        TEXT,
    created_at   DOUBLE PRECISION NOT NULL,
    updated_at   DOUBLE PRECISION NOT NULL,
    token_enc    TEXT,                           -- NEW
    retry_count  INTEGER NOT NULL DEFAULT 0      -- NEW
)

-- SQLite (db.py) — 相同的 ALTER TABLE
ALTER TABLE tasks ADD COLUMN token_enc TEXT;
ALTER TABLE tasks ADD COLUMN retry_count INTEGER DEFAULT 0;
```

### 8.3 迁移

`_init_schema()` / `open_db()` 在建表后检查并执行 `ALTER TABLE ADD COLUMN IF NOT EXISTS`（Postgres 14+ 支持；SQLite 用 `PRAGMA table_info` 检查后手动 ALTER）。

---

## 9. 配置变更

### 9.1 config.yaml

```yaml
# 新增配置段
sync_models:
  deferred: false              # 默认关闭；true = 同步模型走后台 worker
  reconcile_interval: 30       # reconciler 扫描周期 (秒)
  pending_timeout: 60          # pending 超时阈值 (秒)
  running_timeout: 300         # running 超时阈值 (秒)
  max_retries: 3               # 回收最大重试次数
```

### 9.2 环境变量

| 变量 | 用途 |
|---|---|
| `CFGPU_TASK_TOKEN_KEY` | token 加密密钥（base64，32 字节）。`sync_models.deferred=true` 时必需。 |

### 9.3 Settings dataclass

```python
# settings.py

@dataclass
class SyncModelSettings:
    deferred: bool = False
    reconcile_interval: int = 30
    pending_timeout: int = 60
    running_timeout: int = 300
    max_retries: int = 3

@dataclass
class Settings:
    ...
    sync_models: SyncModelSettings = field(default_factory=SyncModelSettings)
```

### 9.4 开启条件

`sync_models.deferred=true` 的前置条件：

1. `task_db.url` 必须是 Postgres（SQLite 单实例不需要 deferred；如果 SQLite + deferred，进程内 worker 可用但无跨实例 reclaim 价值）。
2. `CFGPU_TASK_TOKEN_KEY` 必须设置。

不满足时启动报错，不静默降级。

---

## 10. 代码变更清单

### 10.1 新增文件

| 文件 | 职责 |
|---|---|
| `src/cfgpu_mcp/crypto.py` | `encrypt_token()` / `decrypt_token()`（AES-256-GCM） |
| `src/cfgpu_mcp/reconciler.py` | `Reconciler` 类：周期扫描 + `claim_stale_pending()` + 重试 |

### 10.2 修改文件

| 文件 | 变更 |
|---|---|
| `settings.py` | 新增 `SyncModelSettings`；`load_settings()` 解析 `sync_models` 段 |
| `config.py` | `get_task_repository()` 传入 deferred 配置；新增 `get_reconciler()` 单例 |
| `task_manager.py` | `create()` 新增 deferred 分支；新增 `_run_sync_worker()`；`wait()` 支持 DB 轮询 |
| `client/task_row.py` | `COLUMNS` 增加 `token_enc`, `retry_count` |
| `client/repository.py` | `TaskRepository` ABC 增加 `claim_stale_pending()`、`insert_task()` 支持 `token_enc` |
| `client/postgres_repo.py` | 实现 `claim_stale_pending()`（`FOR UPDATE SKIP LOCKED`）；DDL 加列 |
| `client/db.py` | SQLite DDL 加列；`insert_task()` 支持 `token_enc` |
| `service/task.py` | `get_status()` 跳过 deferred 同步模型的上游 re-poll |
| `http_app.py` | lifespan 启动/停止 reconciler（deferred 模式下） |
| `server.py` | stdio 模式下不启动 reconciler（单实例无意义） |

### 10.3 不需要修改的文件

| 文件 | 理由 |
|---|---|
| `tools/generate.py` | 参数列表不变；`wait=False` 已经透传到 service 层 |
| `tool_registry.py` | `NormalizedResult` 结构不变；input schema 不变 |
| `agent/dispatcher.py` | 调用 service 层，service 签名不变 |
| `adapters/*.py` | `is_async` / `build_payload` / `parse_response` 不变 |
| `models/*/adapter.yaml` | 不新增 `deferred` 字段（deferred 是 config 层开关，不是 per-model） |
| `router.py` | 路由逻辑不变 |

### 10.4 CFGPUClient 小改

`_run_sync_worker` 需要用指定 token 发请求，而不是从 ContextVar 读。两种方式：

- **方案 A**（推荐）：worker 用 `set_request_token(token)` 注入 ContextVar，再用现有 `client.post()`。无需改 `CFGPUClient`。
- 方案 B：给 `CFGPUClient.post()` 加 `token` 参数。侵入性更大。

选方案 A：进程内 worker 在调用 `client.post()` 前先 `set_request_token(captured_token)`；reclaim 路径同理。

---

## 11. 完整流程时序

### 11.1 正常流程（进程内 worker 完成）

```
客户端              实例 A                Postgres
  │                   │                      │
  │ POST generate_    │                      │
  │ image(wait=False) │                      │
  ├──────────────────►│                      │
  │                   │ create()              │
  │                   │  build_payload        │
  │                   │  insert_task(pending) ├──────►
  │                   │  create_task(worker)  │      │
  │                   │  return Task(pending) │      │
  │◄──────────────────┤                      │      │
  │ {task_id, pending} │                      │      │
  │                   │   ┌──────────────────┐│      │
  │                   │   │ worker (后台)     ││      │
  │                   │   │ update(running)  ├┼────►│
  │                   │   │ POST upstream    ││      │
  │                   │   │ parse_response   ││      │
  │                   │   │ update(succeeded)├┼────►│
  │                   │   └──────────────────┘│      │
  │                   │                      │      │
  │ POST task_wait    │                      │      │
  ├──────────────────►│                      │      │
  │                   │ wait()               │      │
  │                   │  poll DB → succeeded  │      │
  │                   │  return result       │      │
  │◄──────────────────┤                      │      │
  │ {urls, ...}       │                      │      │
```

### 11.2 故障恢复流程（实例 A 挂了，实例 B 回收）

```
客户端              实例 A (挂)           实例 B            Postgres
  │                   │                    │                  │
  │ POST generate_    │                    │                  │
  │ image(wait=False) │                    │                  │
  ├──────────────────►│                    │                  │
  │                   │ create()           │                  │
  │                   │  insert(pending)   │                  │
  │                   ├──────────────────────────────────────►│
  │                   │  create_task(worker)│                 │
  │◄──────────────────┤ return pending     │                  │
  │                   │                    │                  │
  │         ══════════╪════════════════════╪════════════════╡进程崩溃
  │                   X                    │                  │
  │                   │            reconciler scan             │
  │                   │            claim_stale_pending(60s)    │
  │                   │            ◄──────────────────────────│ pending row
  │                   │            decrypt token_enc          │
  │                   │            set_request_token(token)    │
  │                   │            update(running)             │
  │                   │            ├──────────────────────────►│
  │                   │            POST upstream               │
  │                   │            parse_response             │
  │                   │            update(succeeded)          │
  │                   │            ├──────────────────────────►│
  │                   │                    │                  │
  │ POST task_wait    │                    │                  │
  ├──────────────────────────────────────►│                  │
  │                   │            wait() → succeeded         │
  │◄───────────────────────────────────────┤                  │
```

### 11.3 running 超时（POST 已发但结果未写回）

```
实例 A 挂在 update(running) 之后、update(succeeded) 之前
  → DB 中 task 停在 running
  → reconciler 看到 running + age > 300s
  → update(failed, error="Worker died after dispatching upstream request")
  → 客户端 task_wait 收到 failed
```

上游已经生成了图片，但结果丢失。客户端需重试（新的 task_id，新的上游请求）。这是**不可恢复的最坏情况**——与当前同步模型实例崩溃的后果一致，但概率更低（只有在 worker 标记 running 之后、写回 succeeded 之前崩溃才会发生）。

---

## 12. 测试策略

### 12.1 单元测试

| 测试 | 覆盖点 |
|---|---|
| `test_deferred_create_returns_pending` | `create()` 对 deferred 同步模型返回 pending，不阻塞 |
| `test_sync_worker_writes_succeeded` | `_run_sync_worker()` 正常执行后 DB 为 succeeded |
| `test_sync_worker_failure_writes_failed` | worker 异常时 DB 为 failed，异常不传播 |
| `test_wait_polls_db_for_deferred` | `wait()` 对 deferred 同步模型轮询 DB 直到终态 |
| `test_get_status_no_upstream_repoll` | `get_status()` 对 deferred 同步模型不调上游 |
| `test_reconciler_claims_stale_pending` | reconciler 回收超时 pending 任务 |
| `test_reconciler_marks_stale_running_failed` | reconciler 标记超时 running 为 failed |
| `test_reconciler_max_retries` | 超过 max_retries 后标 failed |
| `test_token_encryption_roundtrip` | encrypt → decrypt 还原 token |
| `test_claim_skip_locked` | 两个 reconciler 不会认领同一个任务 |

### 12.2 集成测试

| 测试 | 场景 |
|---|---|
| `test_deferred_full_flow` | create(wait=False) → task_wait → 拿到 urls |
| `test_deferred_instance_crash` | 模拟进程崩溃（cancel worker task）→ reconciler 回收 → task_wait 成功 |
| `test_deferred_running_timeout` | worker 标 running 后卡住 → reconciler 标 failed |
| `test_deferred_multi_tenant` | 两个 tenant 各提交一个 deferred 任务，各自 worker 用各自 token |

---

## 13. 风险与权衡

### 13.1 已知限制

| 限制 | 影响 | 缓解 |
|---|---|---|
| running 状态的崩溃不可恢复 | 上游已生成但结果丢失，浪费 credits | 概率低（窗口 < 1s）；与当前同步模型崩溃后果一致 |
| token 加密存储在 DB | 密钥管理负担；DB 备份含密文 | TTL 后清空；AES-256-GCM；密钥不入仓 |
| reconciler 轮询有延迟 | 孤儿任务最多等 `pending_timeout` (60s) 才被回收 | 可调小阈值，但避免误回收正在执行的 worker |
| 进程内 worker 数无上限 | 大量 deferred 请求可能 spawn 无数 asyncio task | 后续可加 semaphore 限制并发 worker 数 |

### 13.2 不做的事

1. **不改 `is_async` 语义**：`is_async` 继续只描述上游 API 行为。deferred 是 cfgpu-mcp 执行策略，正交概念。
2. **不做 per-model deferred 配置**：deferred 是全局开关，不在 adapter.yaml 里配。如果未来需要 per-model 控制（比如只 deferred seedream 不 deferred TTS），再加。
3. **不做外部队列（Celery/RQ/arq）**：`asyncio.create_task` + DB reclaim 足够覆盖目标场景，不引入额外基础设施。如果后续 worker 数量或可靠性要求更高，可替换为外部队列——`_run_sync_worker` 的签名不变，只是调度方式变。
4. **不 deferred `understand` 任务**：vision-language 模型（qwen-3-6-plus）返回文本，通常数秒完成，deferred 收益不大。但代码上不做特殊判断——如果 `deferred=true`，所有 `is_async: false` 的模型都走 deferred。用户可按需开启。

### 13.3 与异步模型的对比

| 维度 | 异步模型 (`is_async=true`) | deferred 同步模型 (`is_async=false, deferred=true`) |
|---|---|---|
| task_id 来源 | 上游返回 | cfgpu-mcp 生成 UUID |
| POST 返回时机 | 秒级（只返回 task_id） | 立即（不 POST，返回 pending） |
| 结果获取 | 轮询上游 `poll_endpoint` | 后台 worker POST 后写 DB |
| `task_status` re-poll | 调上游 GET | 读 DB（不调上游） |
| 崩溃恢复 | 任何实例 re-poll 上游 | 任何实例 reclaim + 重新 POST |
| 重试安全性 | 安全（同 task_id，幂等） | 仅 pending 安全（POST 未发出）；running 不安全 |
| 客户端契约 | `{task_id, status}` → `{urls}` | 完全相同 |

---

## 14. 实施阶段

### Phase 1：进程内 worker（单实例可用）

- `TaskManager.create()` deferred 分支
- `_run_sync_worker()`
- `wait()` DB 轮询
- `get_status()` 跳过 deferred re-poll
- 不需要 token 加密、不需要 reconciler
- 适用：单实例 HTTP 或 stdio（虽然 stdio 无意义，但代码不报错）

### Phase 2：跨实例恢复（多实例必需）

- `crypto.py` token 加密/解密
- `token_enc` 列 + DDL 迁移
- `Reconciler` + `claim_stale_pending()`（`FOR UPDATE SKIP LOCKED`）
- `retry_count` 列
- `http_app.py` lifespan 启动/停止 reconciler

### Phase 3：加固（可选）

- worker 并发 semaphore
- per-task-type `running_timeout`（image 300s, audio 120s）
- metrics（pending queue depth, worker success rate, reclaim count）
- `understand` 任务排除（如果需要）
