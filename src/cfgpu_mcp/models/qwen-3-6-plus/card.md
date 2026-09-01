# Qwen3.6-plus

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | understand (视觉理解 / 图像推理 / 视频理解) |
| CFGPU 模型 ID | `qwen3.6-plus` |
| 能力标签 | image_understanding, image_reasoning, video_understanding, long_video, long_document, tool_calling, visual_agent, long_context, region_understand |
| 调用方式 | 同步（POST `/model/v1/chat/completions` 直接返回结果） |
| 上下文 | 128K |
| 成本档位 | 2/5 |
| 速度档位 | 4/5 |

Qwen3.6原生视觉语言系列Plus模型，展现出与当前顶尖前沿模型相媲美的卓越性能，模型效果相较3.5系列显著提升。模型在Agentic coding、前端编程、Vibe coding等代码能力、多模态万物识别、OCR、物体定位等能力上显著增强。返回的是**文本结果**（理解 / 推理 / 描述），不是图片或视频文件。

## 价格

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 输入长度 (0, 无限] 且输出长度 (0, 无限] | 输入 | 0.0005025 元 / K Tokens |
| | 输出 | 0.005025 元 / K Tokens |

## 参数说明

| 统一 Schema 字段 | chat/completions 映射 | 默认值 | 说明 |
|------------------|------------------------|--------|------|
| prompt | messages[user].content[].text | - | 对图像/视频的指令或问题（必填） |
| images | messages[user].content[].image_url.url | - | 公网图片 URL 列表，图像理解/推理 |
| video | messages[user].content[].video_url.url | - | 单个公网视频 URL，视频理解 |
| regions | （织进 prompt 文本） | - | 用户在 `images` 上圈的区域，见下方「区域理解」 |
| image_refs | （不上行） | - | 调用方对每张图的句柄，仅用于解析 prompt 里的 `[[句柄]]` |
| system_prompt | messages[system].content | `You are a helpful assistant.` | 系统提示词 |
| max_tokens | max_tokens | （模型默认） | 最大输出 token 数 |
| temperature | temperature | （模型默认） | 采样温度 |
| model_specific | （顶层合并） | - | 其他直传参数，如 top_p、tools |

> 返回结构：`choices[0].message.content` 为最终回答（映射为结果 `text`）；
> Thinking 版本另有 `choices[0].message.reasoning_content` 推理过程（映射为
> `reasoning`，在 `return_metadata=true` 时返回）。

## 区域理解（`region_understand`）

用户在图片上圈的框可以直接作为 `regions` 传入，**不需要先把框画到图片上再让模型看**。
qwen3.6-plus 输入输出双向都吃归一 `[0, 999]` 坐标，直接给数字是无损的；把框光栅化
成像素再让模型反推回坐标，是一次纯粹多余的有损往返。

### 坐标形态

| 层 | 形态 |
|----|------|
| 统一 schema (`regions[].box`) | 归一 `[0, 1]` float，`[x1, y1, x2, y2]`，x 先序，左上原点 |
| 落到 prompt | `<bbox>x1 y1 x2 y2</bbox>`，`[0, 999]` 整数格索引 |

换算在 adapter 出口完成一次：左/右边界取 `floor(v*1000)`，右/下边界取
`ceil(v*1000)-1`（**含尾格**——`999` 是最后一格的索引而不是图片右边缘的坐标，
两边同样取整会让整图框 `[0,0,1,1]` 差一格）。

模型也认 `bbox[x1, y1, x2, y2]` 等写法，但我们只发 `<bbox>` 这一种。

### prompt 占位符

| 写法 | 指什么 |
|------|--------|
| `[[标记3]]` | 某个框（本次调用内该名字唯一时） |
| `[[m_a1#标记1]]` | 某个框（跨图重名时的限定写法；`m_a1` 来自 `image_refs`） |
| `[[m_a1]]` | 整张图，渲染成 `图1` 之类的序数 |

每张图的标记名各自从「标记1」开始，所以跨图重名是**合法状态**：裸 `[[标记1]]` 在
两张图都有同名标记时**硬报错并列出限定写法**，绝不取第一个——取错的后果是答的是另
一张图，而那是一个看起来完全合理的答案。没被占位符引用的区域会追加成结构化后缀，
不会被丢掉。

与生图侧不同，这里**会**把标记名和 `note` 一起给模型：它只读不画，没有把名字画进
图里的风险，而带上名字换来的是下面的输出契约。

### 输出契约

`regions` 非空时，adapter 会在 prompt 末尾追加一句「用标记名指代区域、不要输出 bbox
坐标」。这不是洁癖：模型默认会把坐标抄回答案里（实测 `图片1中<bbox>227 71 914
892</bbox>框出的是…`），而那段文本会作为工具结果进入调用方的上下文——正是整套设计
不让坐标出现的地方。更糟的是抄回来的格式恰好是 seedream 能吃的方言，于是「照抄一串
坐标去改图」在一部分模型上真的有效、在另一部分上静默失效。**从源头不产生坐标**，比
产生了再正则过滤好：过滤是个开放匹配问题，漏了是静默的，误伤合法数字更静默。

### 示例

```json
{
  "images": ["https://example.com/room.jpg"],
  "image_refs": ["m_a1"],
  "regions": [
    {"image_index": 0, "box": [0.227, 0.071, 0.915, 0.893], "label": "标记3"}
  ],
  "prompt": "[[标记3]] 里是什么物体？什么材质？和周围的关系是什么？"
}
```

落到 upstream 的 prompt 文本：

```
标记3<bbox>227 71 914 892</bbox> 里是什么物体？什么材质？和周围的关系是什么？

回答时请用标记名（如「标记3」）指代上述区域，不要在回答中输出 bbox 坐标。
```

### 它同时是生图侧的逃生舱

不支持 `region_edit` 的生图模型会**硬报错**而不是静默做整图重绘。报错文案给的第二条
出路就是这里：先用 `understand_vision(images=[...], regions=[...])` 问清每个标记里
是什么、在哪儿、和周围什么关系，再把答案改写成纯自然语言的 prompt（「把左下角压在蓝
色地毯边缘的那辆红色玩具车换成…」），不带 `regions` 重新调用。用语言描述位置本来就
是没有标注时的常规做法，标注只是让这句描述准确。

## 示例

### 图像理解

```json
{
  "stream": false,
  "model": "qwen3.6-plus",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "描述这张图片，并指出其中的异常之处。"},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}}
      ]
    }
  ]
}
```

### 视频理解

```json
{
  "stream": false,
  "model": "qwen3.6-plus",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "请帮我详细描述这个视频里发生了什么，并列出关键事件的时间线。"},
        {"type": "video_url", "video_url": {"url": "https://example.com/clip.mp4"}}
      ]
    }
  ]
}
```
