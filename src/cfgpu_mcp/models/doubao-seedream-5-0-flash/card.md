# Doubao Seedream 5.0 flash

Doubao Seedream 5.0 flash 是面向高频、规模化图片生产与快速交互的同步图像创作模型。它以更高速度、更低成本提供与 Seedream 5.0 Pro 相同的单图创作与编辑能力，包括精准区域编辑、多图融合、图层拆分和透明背景图片输出。

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | image |
| CFGPU 模型 ID | `doubao-seedream-5-0-flash-260915` |
| 同步/异步 | 同步（POST 响应直接返回结果，无 task_id、无需轮询） |
| 能力标签 | text_to_image, image_to_image, multi_image_fusion, region_edit |
| 输出档位 | 1K、1.5K、2K |
| 成本档位 / 速度档位 | 1/5 / 5/5 |

## 价格

| 分辨率 | 计费项 | 价格 |
|------|--------|------|
| 2K | 输出图 | 0.12 元 / 张 |

## 支持范围

- 文生图、单图生图、多图融合与图片编辑。
- 精准区域编辑：统一 `regions` / `image_refs` 参数会像 5.0 Pro 一样转换为 prompt 中的 `<bbox>` 坐标。
- 支持 PNG / JPEG 输出；通过 `model_specific={"output_format": "png"}` 指定。
- 图层拆分：`model_specific={"layer_decomposition": true}`；必须传入一张参考图，prompt 可省略。返回的 `image_items` 保留 `z_index`、`bounding_box`、名称与描述等图层元数据。
- 透明背景：`model_specific={"background": "transparent", "output_format": "png"}`；仅单张带透明通道的输入图可用。
- 支持原生多语种文字生成（俄语、阿拉伯语、泰语、韩语、日语等 14 种语言）。
- 单图模型：不支持组图生成、联网搜索或流式输出；传入 `n > 1` 时只生成一张图。
- 最多 10 张参考图。

## 参数映射

| 统一 Schema 字段 | 上游字段 | 说明 |
|------------------|----------|------|
| prompt | prompt | 直接映射 |
| resolution | size | 支持 `1K`、`1.5K`、`2K`；与 aspect_ratio 一起映射为精确像素值 |
| reference_images | image | 一张时为 string，多张时为 array |
| regions | prompt 内嵌 `<bbox>` | 与 5.0 Pro 的区域编辑坐标格式一致 |
| watermark | watermark | 布尔值，默认 `false` |
| model_specific.output_format | output_format | 可设为 `png` |
| model_specific.background | background | `transparent` 或 `opaque`；透明背景仅限单张透明输入图 |
| model_specific.layer_decomposition | layer_decomposition | `true` 时将单张输入图拆为 1 张底图和最多 16 个透明图层 |

## 调用示例

```bash
curl --location 'https://api.cfgpu.com/userapi/v1/images/generations' \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <API-TOKEN>' \
  --data '{
    "model": "doubao-seedream-5-0-flash-260915",
    "prompt": "充满活力的特写编辑肖像，模特眼神犀利，头戴雕塑感帽子，色彩拼接丰富，眼部焦点锐利，景深较浅，具有Vogue杂志封面的美学风格，采用中画幅拍摄，工作室灯光效果强烈。",
    "size": "2K",
    "output_format": "png",
    "watermark": false
  }'
```
