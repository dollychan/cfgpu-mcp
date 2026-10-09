# Doubao Seedance 2.5

豆包大模型团队推出的新一代专业级多模态视频创作模型。支持单段 **30 秒**视频直出，单次最多可输入 **50 个参考素材**（30 张图 + 10 个视频 + 10 个音频），具备更强的指令控制、专业级可控的视频编辑与延长能力；同时原生支持生成 10 余种语言的人声叙事，让视频生成迈入「长叙事 × 强参考 × 准编辑 × 多语言」阶段。

> **API 形态等同 WAN 2.0 / Seedance 2.0。** 请求体（`content` 多模态数组 + 顶层 `ratio` / `duration` / `resolution` / `generate_audio` / `watermark`）与返回结构完全一致，差异在**规模**（更长时长、更多参考素材、更强编辑与多语言）和参数范围。完整的 content 输入类型、各场景示例与响应结构详见 `wan-video` 的 card.md，此处只列本模型的标识、容量与计价差异。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | video |
| CFGPU 模型 ID | `doubao-seedance-2-5` |
| 任务（tasks） | text_to_video, image_to_video, first_last_frame, reference_to_video, video_edit, video_extend, synced_audio_output |
| 成本档位 | 4/5 |
| 速度档位 | 2/5 |

## 价格（按 token 计费）

| 分辨率范围 | 场景 | 单价 |
|-----------|------|------|
| (0, 无限] | 有视频输入的有声视频 | 0.0441 元 / K tokens |
| (0, 无限] | 有视频输入的无声视频 | 0.0441 元 / K tokens |
| (0, 无限] | 没有视频输入的有声视频 | 0.0735 元 / K tokens |
| (0, 无限] | 没有视频输入的无声视频 | 0.0735 元 / K tokens |

> 与 2.0 不同，2.5 **不按分辨率分档**：只按「是否有视频输入」区分单价，有视频输入更便宜。Token 消耗见响应结构中的 `usage.totalTokens` 字段。

## 相对 Seedance 2.0 的差异

| 维度 | Seedance 2.0 | **Seedance 2.5** |
|------|--------------|------------------|
| 单段时长 | 4–15 秒（或 `-1` 智能） | **4–30 秒**（或 `-1` 智能） |
| 参考图片 | 0–9 张 | **0–30 张** |
| 参考视频 | 最多 3 个 | **最多 10 个** |
| 参考音频 | 最多 3 段 | **最多 10 段** |
| 单次参考素材总数 | ≤ 15 | **≤ 50** |
| 分辨率 | 480p / 720p / 1080p / 4k | **480p / 720p / 1080p**（默认 720p） |
| 多语言人声 | — | **原生支持 10 余种语言** |
| 指令控制 / 编辑 | 专业级 | 更强的指令遵循与更精准的编辑、延长 |
| 计价 | 按分辨率分档 | 不分辨率分档，按有无视频输入分档 |

其余（首尾帧规则、`role` 取值、素材格式要求、ratio 取值、联网搜索、watermark、prompt 写法）与 Seedance 2.0 / WAN 2.0 一致。

> **分辨率**：2.5 支持 `480p`、`720p`、`1080p`，默认 `720p`。

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_video** | 纯文本生成视频，可直出 30 秒长叙事 |
| **image_to_video** | 单张首帧图片 + 文本生成视频 |
| **first_last_frame** | 首帧 + 尾帧图片 + 文本生成视频（精准控制起止画面） |
| **reference_to_video** | 多模态参考生视频：图片(0-30) + 视频(0-10) + 音频(0-10) + 文本，单次总计 ≤ 50 |
| **video_edit** | 基于参考视频进行专业级可控编辑（替换元素、修改内容） |
| **video_extend** | 延长已有视频时长 |
| **synced_audio_output** | 生成与画面同步的有声视频（人声、音效、背景音乐），原生支持 10 余种语言 |

> 支持仅传入参考音频；文本提示词可选。

## 参数与统一 Schema 映射

| 统一 Schema 字段 | API 字段 | 说明 |
|------------------|----------|------|
| `prompt` | `content[].type=text` | 文生视频必填；其余任务可选。30 秒长叙事建议按 `[0-10秒] / [10-20秒] / [20-30秒]` 分段描述 |
| `first_frame` | `content[].role=first_frame` | 首帧图片 |
| `last_frame` | `content[].role=last_frame` | 尾帧图片（需与 first_frame 同用） |
| `reference_images` | `content[].role=reference_image` | 参考图片（0-30，与首/尾帧互斥） |
| `reference_videos` | `content[].role=reference_video` | 参考视频（0-10） |
| `reference_audios` | `content[].role=reference_audio` | 参考音频（0-10） |
| `aspect_ratio` | 顶层 `ratio` | 文生/普通参考任务支持指定宽高比或 `adaptive`；首帧/首尾帧任务仅支持 `adaptive`，输出比例跟随首帧 |
| `duration_seconds` | 顶层 `duration` | 默认 `-1`；取值 4–30 秒，或 `-1` 智能选择 |
| `resolution` | 顶层 `resolution` | 480p / 720p / 1080p，默认 720p |
| `with_audio` | 顶层 `generate_audio` | 是否生成有声视频 |
| `watermark` | 顶层 `watermark` | 是否添加水印 |
| —（自动推导） | 顶层 `omni_reference_task_type` | 仅全模态参考任务发送；按最终参数推导为 `reference` / `auto`，可用 `model_specific` 显式声明 `edit` / `extend`，见下文 |

### 视频编辑任务的 duration 限制

- `duration_seconds` 仅允许设置为 `-1`，不支持指定具体输出时长。
- 待编辑源视频时长必须在 `[4, 30]` 秒内，否则上游会报错。
- 当前统一输入用同一个 `reference_videos` 字段承载“参考生视频”和“视频编辑”，任务由上游按内容与意图判定；本地无法仅凭 URL 可靠读取源视频时长。

### 全模态子任务（`omni_reference_task_type`）

全模态参考任务按提示词意图分为三类子任务，约束各不相同：

| 子任务 | `aspect_ratio` | `duration_seconds` | 参考视频 | 提示词关键词 |
|--------|---------------|--------------------|---------|-------------|
| 参考生视频 | 任意 | 任意 | 可选 | — |
| 视频延长 | 必须 `adaptive` | 4–30 或 `-1` | 至少 1 个 | 向前/向后延长、延续、续写 |
| 视频编辑 | 必须 `adaptive` | 必须 `-1` | 至少 1 个（4–30 秒） | 编辑视频、增加/加上、删除/去掉、修改/替换/改成 |

MCP 在提交时按最终参数自动填写该字段：无参考视频或 `aspect_ratio` 非 `adaptive` → `reference`；其余 → `auto`（由模型判定）；文生视频、首帧/首尾帧不发。

- **做编辑/延长时**：保持 `aspect_ratio="adaptive"`（编辑还需 `duration_seconds=-1` 或不传），提示词里写明对应关键词。
- **只想把视频当参考**：指定具体宽高比（如 `16:9`）即可，会自动声明为 `reference`，模型不会误判为编辑/延长。
- **显式声明**：`model_specific={"omni_reference_task_type": "edit" | "extend" | "reference" | "auto"}`，会覆盖自动推导；与上表冲突时在发请求前报错。
- 在 `auto` 下被判为编辑/延长但参数不满足时，上游会**异步**报 `InvalidParameter.TaskTypeConstraint`；声明的类型与提示词意图不一致时报 `InvalidParameter.TaskTypeMismatch`。这两种错误会以 `invalid_params` 返回并附具体改法；原样重试不会成功，按上表调整参数或改写提示词后重新提交。

## Prompt 建议（30 秒长叙事）

30 秒是单镜头模型少见的长度，直接写一句概括容易让中后段失控。按时间段分镜描述，并在段与段之间写明转场方式，效果最稳：

```
[0-10秒]：<画面/主体/运镜>
[10-20秒]：<如何从上一段无缝过渡 + 新画面>
[20-30秒]：<如何收尾，回到开场元素形成闭环>
技术规格：<材质、色调、景深、氛围>
```

引用参考素材时在文本里直接点名（如「参考@图像1」「全程使用视频1的第一视角构图」），指令遵循比 2.0 更准。有声视频把台词放进双引号，模型会按语言自动配音。

## 示例

### 30 秒 + 参考图（异步任务）

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/video/generations' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer <API-TOKEN>' \
    -d '{
    "model": "doubao-seedance-2-5",
    "content": [
        {
            "type": "text",
            "text": "一段高级、极具电影感的30秒3D动态图形序列，精致的蒸汽朋克与复古微缩景观风格，连续流畅的环绕与穿透运镜。[0-10秒]：古董黄铜钟面微距特写，层层展开为相互啮合的旋转齿轮环与体积雾……[10-20秒]：镜头跟随扑翼机的轨迹向前滑行，无缝穿透入一个高速旋转的黄铜幻影箱……[20-30秒]：镜头优雅向下平移，出现发条木制机械帆船破浪前行，最后回到滴答作响的黄铜钟面，最后一秒出现 logo，参考@图像1。技术规格：超写实机械纹理，丰富黄铜与金色调，电影级浅景深。"
        },
        {
            "type": "image_url",
            "image_url": {
                "url": "https://arkdocs.tos-cn-beijing.volces.com/images/video-generation/seedance2.5_30s_input.png"
            },
            "role": "reference_image"
        }
    ],
    "generate_audio": true,
    "ratio": "16:9",
    "duration": 30
}'
```

### 查询任务

```bash
curl --location 'https://www.cfgpu.com/userapi/v1/video/tasks/<TASK_ID>' \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer <API-TOKEN>'
```

首尾帧、视频编辑、视频延长等其余场景的完整请求示例与响应结构，请参见 `wan-video` 的 card.md。
