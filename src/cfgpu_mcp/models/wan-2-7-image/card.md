# 万相 2.7 图像生成与编辑 (wan2.7-image)

万相 2.7 图像模型，**同步**返回结果：一次 POST 直接拿到图片 URL，无需轮询。支持文生图、
文生组图、图生组图、图像编辑、多图参考生成、交互式（框选）编辑；在文字渲染、主体一致性、
复杂指令遵循上比上一代更强。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | image |
| CFGPU 模型 ID | `wan2.7-image` |
| 同步/异步 | 同步（`is_async: false`，POST 响应即返回结果） |
| 能力标签 | text_to_image, image_to_image, multi_image_fusion, multi_image_group, region_edit |
| 成本档位 | 2/5 |
| 速度档位 | 2/5 |

## 价格

| 条件 | 计费项 | 价格 |
|------|--------|------|
| 分辨率 (0, 无限] | 输出图 | 0.2 元 / 张 |

> **按张计费**：费用 = 单价 x 成功生成的图片数量。开了图像集（`n>1`）就可能一次出多张，
> `n` 是上限也是费用上限。

## 能力说明

| 能力 | 说明 |
|------|------|
| **text_to_image** | 纯文本生成单张图像 |
| **image_to_image** | 单张参考图 + 文本编辑/生成 |
| **multi_image_fusion** | 多张参考图（最多 9 张）+ 文本生成单张图片，prompt 里用「图1」「图2」指代 |
| **multi_image_group** | 图像集（`enable_sequential`）：一次生成一组风格/主体一致的图片 |
| **region_edit** | 交互式编辑：在输入图上框选区域，只改框里的内容；坐标走结构化的 `bbox_list` 字段 |

### 不支持的能力

- 无 4K：`4K` 只有 `wan2.7-image-pro` 的文生图场景支持，本模型全场景上限 2K。
- 无异步任务：cfgpu 走同步接口，没有 `task_id`，`task_status` / `task_wait` 用不上。

## 参数说明

请求是 DashScope 风格的嵌套信封（和万相 2.7 视频家族一样，和 Seedream 的扁平结构不同）：

```
{"model", "input": {"messages": [{"role": "user", "content": [...]}]}, "parameters": {...}}
```

`content` 数组里**有且仅有一个** `text` 对象，外加 0–9 个 `image` 对象。**图片在数组里的
顺序就是它的序号**：`reference_images[0]` 就是 prompt 里的「图1」，也是 `bbox_list` 第 0 项
对应的那张图。

| 统一 Schema 字段 | wan2.7-image 字段 | 映射说明 |
|------------------|-------------------|----------|
| prompt | `input.messages[0].content[].text` | 中英文均可，上限 5000 字符（超出部分上游自动截断） |
| reference_images | `input.messages[0].content[].image` | 公开 URL；最多 9 张 |
| resolution + aspect_ratio | `parameters.size` | 查表成精确像素对，见下方「尺寸」 |
| n | `parameters.n` + `parameters.enable_sequential` | `n>1` 时才下发，且两个键**永远一起**下发，见下方「图像集」 |
| regions | `parameters.bbox_list` | 归一坐标换算成**原图绝对像素**，见下方「交互式编辑」 |
| watermark | `parameters.watermark` | 右下角「AI生成」文字水印，默认 `false` |
| quality_tier | `parameters.thinking_mode` | `fast` → `false`，其余 → `true`；仅在纯文生图（无图片输入且非图像集）时下发，因为上游只在该场景生效 |
| model_specific | （深合并） | `{"parameters": {...}}` 会**并入** `parameters` 而不是整个替换掉它；其他键在顶层合并 |

> `seed`（[0, 2147483647]）和 `color_palette`（3–10 个 `{hex, ratio}`，`ratio` 之和须为
> 100.00%，且仅在非图像集模式可用）统一 schema 里没有对应字段，用
> `model_specific={"parameters": {"seed": 42}}` 下发。

### 尺寸

`size` 有两种写法且不可混用：档位名（`1K` / `2K`，默认 `2K`），或 `宽*高` 精确像素对
（注意分隔符是 `*`，不是 Seedream 的 `x`）。**cfgpu 始终发精确像素对**：统一 schema 里
`aspect_ratio` 是必有的显式参数，只有像素能真正兑现它。

和 Seedream 不同，官方没有公布逐档位逐画幅的像素表，公布的是**每档的总像素预算**
（1K = 1024x1024，2K = 2048x2048）和全场景的总像素区间 [768x768, 2048x2048]。所以下表是
**算出来的**：对画幅 aw:ah，取满足预算的最大整数 m，宽高 = (2·aw·m, 2·ah·m)——宽高比因此
是**精确**的 aw:ah，两边都是偶数，每格都用掉了本档 96.9% 以上的预算。

| aspect_ratio | 1K | 2K |
|--------------|----|----|
| `1:1` | `1024*1024` | `2048*2048` |
| `4:3` | `1176*882` | `2360*1770` |
| `3:4` | `882*1176` | `1770*2360` |
| `16:9` | `1344*756` | `2720*1530` |
| `9:16` | `756*1344` | `1530*2720` |
| `3:2` | `1254*836` | `2508*1672` |
| `2:3` | `836*1254` | `1672*2508` |
| `21:9` | `1554*666` | `3108*1332` |

> **有图片输入时也发像素**：这是本仓库所有图像 adapter 的一致做法，但有一个要知道的后果——
> 如果发的是档位名，上游会把输出**按最后一张输入图片的宽高比**缩放；发像素则由
> `aspect_ratio`（默认 `1:1`）说了算，编辑一张 16:9 的图也会得到 1:1。想把画幅交还给模型，
> 用 `model_specific={"parameters": {"size": "2K"}}` 覆盖成档位名。

### 图像集（`n` / `enable_sequential`）

`n>1` 会同时下发 `enable_sequential: true` 和 `n`。**`n` 是上限，不是张数**：模型自己决定
这组出几张，只保证不超过 `n`，少于 `n` 是正常结果而不是失败——别在结果回来之前向用户承诺
具体张数。

两个键**永远一起**下发：`enable_sequential: true` 而不带 `n` 时上游的默认值是 **12**，那是
12 张的账单。`n=1` 时两个键都不发，保持上游默认（`enable_sequential: false`, `n=1`）。

> 上游在非图像集模式下也允许 `n` 取 1–4（同一 prompt 出多张独立图片），但统一 schema 里的
> `n` 语义是「一组相关图片」，所以本 adapter 只用图像集这一条路径。`n` 上限 12。

### 交互式编辑（`region_edit`）

统一 schema 侧照常传 `regions`，adapter 负责换算与织入：

| 层 | 形态 |
|----|------|
| 统一 schema (`regions[].box`) | 归一 `[0, 1]` float，`[x1, y1, x2, y2]`，x 先序，左上原点 |
| 落到 `parameters.bbox_list` | `[[x1,y1,x2,y2], ...]` 逐图分组，**原图绝对像素**整数 |

- **外层列表与图片槽位一一对齐**，没有框的图片补 `[]`。这个补位由 adapter 做：调用方传扁平
  的 `regions`（每个带 `image_index`），少写一项不会报错，只会把框落到另一张图上。
- **单张图最多 2 个框**（`max_regions_per_image: 2`），超出在本地就被拒绝。
- **必须带 `image_size`**：`bbox_list` 要的是原图绝对像素，而尺寸绝不猜测——猜错不会报错，
  只会在图上另一个位置改出一张看着合理、还要计费的图。缺 `image_size` 的请求在
  `supports()` 就被拒（`model="auto"` 会因此绕开本模型，改走 prompt 内嵌坐标的
  `doubao-seedream-5-0-pro`，那种方言不需要原图尺寸）。
- **换算规则（含尾格）**：左/上取 `floor(v*w)`，右/下取 `ceil(v*w)-1`。和 `[0, 999]` 网格方言
  用的是同一个函数（网格就是这个换算跑在 1000x1000 的图上），off-by-one 会跟着每次编辑往下漂。
- **不能和 `n>1` 一起用**：组图是几张互相独立的图，框标的是「正在编辑的这张图上的某个位置」，
  上游也没有定义两者的交互。

#### prompt 占位符

坐标走结构化字段，但**句子里仍然需要一个指代**，否则模型不知道那句指令挂在哪个框上。所以
`[[…]]` 占位符照常写，只是替换出来的不是坐标而是中性说法：

| 写法 | 渲染成 |
|------|--------|
| `[[标记1]]` | `图2中框选的区域`（该图只有一个框时） |
| `[[标记1]]` | `图2中框选的第1个区域`（该图有两个框时） |
| `[[m_a1#标记1]]` | 同上（跨图重名时的限定写法；`m_a1` 来自 `image_refs`） |
| `[[m_a1]]` | `图1`（整张图的序数） |

- **裸名字跨图重名 → 硬报错**并列出限定写法，和 prompt 坐标方言完全一致。
- **没被占位符引用的区域不会被丢掉，也不会被追加成后缀**：它本来就在 `bbox_list` 里到了模型
  手上。prompt 坐标方言追加后缀是因为 prompt 是那些框**唯一**的通道；这里再追加一句
  「图1中框选的第2个区域。」，不是兜底而是多一条指令。
- **标记名和 `note` 不进 prompt**：本模型是生图模型，prompt 里出现「标记1」有被**画进结果图**
  的风险。要按名字说话的是理解模型（`understand_vision`），不是这里。

## 请求示例

### 文生图

```json
{
  "model": "wan2.7-image",
  "input": {
    "messages": [
      {"role": "user", "content": [{"text": "一间有着精致窗户的花店，漂亮的木质门，摆放着花朵"}]}
    ]
  },
  "parameters": {"size": "2048*2048", "watermark": false, "thinking_mode": true}
}
```

### 图像集（文生组图）

```json
{
  "model": "wan2.7-image",
  "input": {
    "messages": [
      {"role": "user", "content": [{"text": "电影感组图，记录同一只流浪橘猫的四季，特征需全程一致……"}]}
    ]
  },
  "parameters": {"size": "2048*2048", "watermark": false, "enable_sequential": true, "n": 4}
}
```

### 多图编辑

```json
{
  "model": "wan2.7-image",
  "input": {
    "messages": [
      {"role": "user", "content": [
        {"text": "把图2的涂鸦喷到图1的车上"},
        {"image": "https://example.com/car.webp"},
        {"image": "https://example.com/paint.webp"}
      ]}
    ]
  },
  "parameters": {"size": "2720*1530", "watermark": false}
}
```

### 交互式（框选）编辑

图1 不框、图2 框一个区域，`bbox_list` 与图片顺序一一对齐：

```json
{
  "model": "wan2.7-image",
  "input": {
    "messages": [
      {"role": "user", "content": [
        {"text": "把图1的闹钟放到图2中框选的区域，光影自然融合"},
        {"image": "https://example.com/clock.webp"},
        {"image": "https://example.com/room.webp"}
      ]}
    ]
  },
  "parameters": {"size": "2720*1530", "watermark": false, "bbox_list": [[], [[989, 515, 1138, 681]]]}
}
```

## 响应结构

```json
{
  "output": {
    "choices": [
      {"finish_reason": "stop",
       "message": {"role": "assistant",
                   "content": [{"image": "https://dashscope-result.oss-cn-shanghai.aliyuncs.com/xxx.png?Expires=xxx",
                               "type": "image"}]}}
    ],
    "finished": true
  },
  "usage": {"image_count": 1, "input_tokens": 18790, "output_tokens": 2, "size": "2985*1405", "total_tokens": 18792},
  "request_id": "a3f4befe-cacd-49c9-8298-xxxxxx"
}
```

| 字段 | 说明 |
|------|------|
| `output.choices[].message.content[].image` | 生成图片 URL（**24 小时后失效**，请及时下载保存） |
| `usage.image_count` | 实际生成张数（图像集下可能少于 `n`） |
| `usage.size` | 实际输出尺寸，可能与请求的 `size` 略有差异 |

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| prompt 长度 | ≤ 5000 字符（超出自动截断） |
| 输入图片数量 | ≤ 9 张 |
| 输入图片格式 | JPEG / JPG / PNG（不支持 Alpha 通道）/ BMP / WEBP |
| 输入图片分辨率 | 宽高各 240–8000 像素，宽高比在 [1:8, 8:1] |
| 输入图片大小 | ≤ 20 MB |
| 输出总像素 | [768x768, 2048x2048]，宽高比在 [1:8, 8:1] |
| 单图框选数量 | ≤ 2 个 |
| `n` | 图像集模式 1–12 |
| 图片链接有效期 | 24 小时 |

