# Kling Video O1 (可灵 O1)

可灵 O1（可灵视频 O1 模型）是可灵 AI 推出的全球首个统一多模态视频生成模型。模型通过创新的多模态视觉语言（MVL）架构，实现视频生成、编辑与理解的无缝融合，支持图片、视频和文字等多模态输入，能进行全能创作编辑，解决视频一致性难题，提供多种创意组合。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| adapter_id | `kling-video-o1` |
| CFGPU 模型 ID | `kling-video-o1` |
| 能力标签 | text_to_video |
| 成本档位 | 4/5 |
| 速度档位 | 2/5 |

## 价格

按秒计费，分辨率与是否含音频/视频输入分档：

| 分辨率 | 场景 | 单价 |
|--------|------|------|
| (0, 720P] | 有视频输入的有声视频 | 0.945 元 / 秒 |
| (0, 720P] | 有视频输入的无声视频 | 0.945 元 / 秒 |
| (0, 720P] | 没有视频输入的有声视频 | 0.63 元 / 秒 |
| (0, 720P] | 没有视频输入的无声视频 | 0.63 元 / 秒 |
| (720P, ∞) | 有视频输入的有声视频 | 1.26 元 / 秒 |
| (720P, ∞) | 有视频输入的无声视频 | 1.26 元 / 秒 |
| (720P, ∞) | 没有视频输入的有声视频 | 0.84 元 / 秒 |
| (720P, ∞) | 没有视频输入的无声视频 | 0.84 元 / 秒 |

## 参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| model | string | ✓ | - | 固定值：`kling-video-o1` |
| prompt | string | ✓ | - | 视频描述，支持中英文 |
| size | string | - | 1280x720 | 输出像素尺寸 `宽x高`，由统一 Schema 的 `resolution` + `aspect_ratio` 映射得到 |
| mode | string | - | std | 生成模式：`std`（标准）/ `pro`（高质量），由 `quality_tier` 映射（`best` → `pro`） |
| seconds | string | ✓ | "5" | 视频时长（秒），字符串形式 |

## 与统一 Schema 的映射

| 统一 Schema 字段 | Kling 字段 | 映射说明 |
|------------------|------------|----------|
| prompt | prompt | 直接透传 |
| resolution + aspect_ratio | size | 映射成像素 `宽x高`，`aspect_ratio=adaptive` 时按 16:9 处理 |
| quality_tier | mode | `best` → `pro`，其余 → `std` |
| duration_seconds | seconds | 转成字符串透传；不支持 `-1` 智能时长 |
| model_specific | -（合并到顶层） | 末位合并，可覆盖上述字段 |

**分辨率 → size 对照（部分）：**

| resolution | aspect_ratio | size |
|------------|--------------|------|
| 480p | 16:9 | 854x480 |
| 720p | 16:9 | 1280x720 |
| 720p | 9:16 | 720x1280 |
| 1080p | 16:9 | 1920x1080 |
| 1080p | 9:16 | 1080x1920 |
| 1080p | 1:1 | 1080x1080 |

## 能力与限制

- 目前 adapter 仅支持**文生视频**（text_to_video）。可灵 O1 的图片/视频/音频参考输入（图生视频、参考生视频）依赖尚未公开的字段约定，因此 `supports()` 会拒绝 `first_frame` / `last_frame` / `reference_*` 输入，待官方补齐文档后再扩展。
- 需要显式时长，不支持 `duration_seconds=-1`。

## 异步任务流程

可灵 O1 为异步接口，需轮询查询状态：

1. **创建任务**：POST `/video/generations`，返回 `id`（task_id）
2. **查询状态**：GET `/video/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务 `succeeded` 后从 `content.videoUrl` 取视频 URL（24 小时内有效）

## 示例

### 文生视频

```json
{
  "model": "kling-video-o1",
  "prompt": "一只可爱的橘猫在阳光下奔跑，慢镜头，电影质感",
  "size": "1920x1080",
  "mode": "pro",
  "seconds": "5"
}
```

## 响应结构

查询任务结果 GET `/video/tasks/{task_id}` 返回标准视频任务结构：

```json
{
  "id": "cgt-xxx",
  "model": "kling-video-o1",
  "status": "succeeded",
  "content": {
    "videoUrl": "https://...",
    "lastFrameUrl": null
  },
  "seed": 15233,
  "usage": {
    "totalTokens": null
  }
}
```
