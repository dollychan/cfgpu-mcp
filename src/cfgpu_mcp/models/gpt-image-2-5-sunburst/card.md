# CF Image 2.5 Sunburst

## 基本信息

| 属性 | 值 |
|------|-----|
| CFGPU 模型 ID | `gpt-image-2.5-sunburst` |
| MCP modelName | `cf-image-2.5-sunburst` |
| Provider | `cfgpu` |
| 任务类型 | image（异步） |
| 任务（tasks） | text_to_image, image_to_image, multi_image_fusion |

`CF Image 2.5 Flare`（`cf-image-2.5-flare`）使用完全相同的接口和参数范围，只是上游模型 ID
为 `gpt-image-2.5-flare`。

## 参数说明

| API 字段 | 必填 | 取值 / 说明 |
|---|---:|---|
| `prompt` | 是 | 文本提示词。 |
| `image_size` | 否 | 不开放。cfgpu 不下发该字段，输出尺寸由上游按 `aspect_ratio` + `resolution` 映射。 |
| `aspect_ratio` | 否 | `1:1`、`3:2`、`2:3`、`4:3`、`3:4`、`16:9`、`9:16`、`21:9`、`9:21`、`3:1`、`1:3`、`2:1`、`1:2`。 |
| `quality` | 否 | 由 `quality_tier` 控制：`fast` → `low`、`balanced` → `medium`、`best` → `high`，`xhigh` / `max` / `auto` 原样下发（`auto` 按 medium 预估，完成后按实际用量结算）。`xhigh` / `max` / `auto` 只有本系列支持，其它图片模型会拒绝。 |
| `resolution` | 否 | `1K`、`2K`、`4K`，默认 `1K`。 |
| `reference_images` | 否 | 参考图片 URL 数组。 |
| `background` | 否 | 背景模式：`auto`、`opaque`、`transparent`；默认 `auto`，通过 `model_specific.background` 覆盖。 |

`n` 未在该接口中提供，cfgpu 因而只接受单图请求（`n=1`）。

## MCP 调用示例

```json
{
  "model": "cf-image-2.5-sunburst",
  "prompt": "一只可爱的猫咪",
  "aspect_ratio": "1:1",
  "resolution": "1K",
  "quality_tier": "balanced",
  "model_specific": {"background": "auto"}
}
```

## API

- 创建任务：`POST https://api.cfgpu.com/userapi/v1/images/generations`
- 查询任务：`GET https://api.cfgpu.com/userapi/v1/images/tasks/{task_id}`

两者均使用 `Authorization: Bearer <API_KEY>` 和 `Content-Type: application/json`。响应结构与
[CF Image 2](../gpt-image-2/card.md) 的异步任务结构一致：创建返回 `data.task_id`，完成后从
`data.result.images` 读取图片 URL。

- 响应结构
```json
{"code":200,"data":{"result":{"images":["https://smartmlxxx"],
"tokenUsage":{"imageInputTokens":0,"textInputTokens":987,"totalTokens":3689,"imageOutputTokens":2702},
"source_images":["https://smartmlxxx"],
"tokenBillingSettlement":{"actualPoints":40,"supplementPoints":1,"prechargePoints":39,"channel":"official","refundPoints":0},
"proxy_images":["https://smartml-xxx"]},"updated_at":"2026-10-15T19:07:27.000Z","created_at":"2026-10-15T19:06:47.000Z","token_billing_settlement":{"refund_points":0,"actual_points":40,"channel":"official","estimate_tokens":{"imageInputTokens":0,"textInputTokens":920,"totalTokens":3622,"imageOutputTokens":2702},"supplement_points":1,"prices":{"textInput":2270,"imageInput":3640,"imageOutput":13650,"minPoints":1},"precharge_points":39,"points_refunded":false},"task_id":"21dcc2d0-5a3e-480b-a9d0-3eccb0e95dc7","task_type":"gpt_image_generation","status":"completed"},"model":"gpt-image-2.5-flare","message":"success"}
```