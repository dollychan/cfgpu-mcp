# Kling V3 Omni (可灵 V3 全能版)

Kling-V3-Omni 是全能多模态版本，将文/图生视频、视频编辑以及基于多参考图的角色和风格一致性控制，完美统一在了单一模型中。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `kling-v3-omni` |
| 能力标签 | text_to_video |
| 成本档位 | 5/5 |
| 速度档位 | 2/5 |

## 价格

按秒计费，分辨率与是否含音频/视频输入分档：

| 分辨率 | 场景 | 单价 |
|--------|------|------|
| (0, 720P] | 有视频输入的有声视频 | 0.945 元 / 秒 |
| (0, 720P] | 有视频输入的无声视频 | 0.945 元 / 秒 |
| (0, 720P] | 没有视频输入的有声视频 | 0.84 元 / 秒 |
| (0, 720P] | 没有视频输入的无声视频 | 0.63 元 / 秒 |
| (720P, 1080P] | 有视频输入的有声视频 | 1.26 元 / 秒 |
| (720P, 1080P] | 有视频输入的无声视频 | 1.26 元 / 秒 |
| (720P, 1080P] | 没有视频输入的有声视频 | 1.05 元 / 秒 |
| (720P, 1080P] | 没有视频输入的无声视频 | 0.84 元 / 秒 |
| (1080P, ∞) | 统一计价 | 3.15 元 / 秒 |

## 参数说明

与 [可灵 O1](../kling-video-o1/card.md) 共用同一套 flat 请求格式（由 `KlingVideoAdapter` 通过 extends 链复用）：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | ✓ | - | 固定值：`kling-v3-omni` |
| prompt | string | ✓ | - | 视频描述，支持中英文 |
| size | string | - | 1280x720 | 输出像素尺寸 `宽x高`，由统一 Schema 的 `resolution` + `aspect_ratio` 映射得到 |
| mode | string | - | std | 生成模式：`std` / `pro`，由 `quality_tier` 映射（`best` → `pro`） |
| seconds | string | ✓ | "5" | 视频时长（秒），字符串形式 |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Kling 字段 | 映射说明 |
|------------------|------------|----------|
| prompt | prompt | 直接透传 |
| resolution + aspect_ratio | size | 映射成像素 `宽x高`，`aspect_ratio=adaptive` 时按 16:9 处理 |
| quality_tier | mode | `best` → `pro`，其余 → `std` |
| duration_seconds | seconds | 转成字符串透传；不支持 `-1` 智能时长 |
| model_specific | -（合并到顶层） | 末位合并，可覆盖上述字段 |

## 能力与限制

- 目前 adapter 仅支持**文生视频**（text_to_video）。V3 Omni 的图生视频、视频编辑、多参考图一致性控制依赖尚未公开的字段约定，因此 `supports()` 会拒绝 `first_frame` / `last_frame` / `reference_*` 输入，待官方补齐文档后再扩展。
- 需要显式时长，不支持 `duration_seconds=-1`。

## 示例

### 文生视频

```json
{
  "model": "kling-v3-omni",
  "prompt": "一只可爱的橘猫在阳光下奔跑，慢镜头，电影质感",
  "size": "1920x1080",
  "mode": "pro",
  "seconds": "5"
}
```

## 异步任务流程

可灵 V3 Omni 为异步接口，需轮询查询状态：

1. **创建任务**：POST `/video/generations`，返回 `id`（task_id）
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务 `succeeded` 后从 `content.videoUrl` 取视频 URL（24 小时内有效）
