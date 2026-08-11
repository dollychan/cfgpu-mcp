# GPT Image 2

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | image |
| CFGPU 模型 ID | `gpt-image-2` |
| 成本档位 | 2/5 |
| 速度档位 | 3/5 |

## 价格

| 分辨率范围 | 单价 |
|-----------|------|
| (0, 1K] | 0.105 元 / 张 |
| (1K, 2K] | 0.16 元 / 张 |
| (2K, 无限] | 0.21 元 / 张 |

## 能力

- **文生图**：文本描述生成图片
- **图生图**：参考图 + 文本提示编辑或变换图片

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | string | 必填 | 图片描述 |
| `model` | string | - | 使用 `gpt-image-2` |
| `aspect_ratio` | string | `1:1` | 1:1、3:2、2:3、4:3、3:4、16:9、9:16 |
| `resolution` | string | `""`（=1K） | 分辨率档位，仅 `2K` / `4K` 有显式取值，空串为默认 1K |
| `reference_images` | list[url] | 可选 | 参考图 URL 数组，传入后进入图片编辑模式 |
| `model_specific` | dict | 可选 | 透传到 API 的额外参数 |

### 统一 Schema 映射

| 统一 Schema | API 字段 | 说明 |
|---|---|---|
| `resolution` | `resolution` | `1K` → `""`（API 用空串表示默认档）；`2K` / `4K` 原样下发；`3K` 无对应档位，原样下发由上游拒绝 |
| `aspect_ratio` | `aspect_ratio` | 原样下发。统一 Schema 多出的 `21:9` 不在本模型取值集内，不做本地拦截，由上游拒绝 |
| `watermark` / `n>1` | — | 不支持：`watermark` 忽略，`n>1` 由 `supports()` 本地拒绝 |

## 使用示例

**文生图**
```json
{
  "prompt": "一只可爱的猫咪，写实风格",
  "model": "gpt-image-2"
}
```

**图生图**
```json
{
  "prompt": "将图片风格改为水彩画",
  "model": "gpt-image-2",
  "reference_images": ["https://example.com/input.jpg"]
}
```

## 响应结构

图片创建结果
```json
{
  "code":200,
  "data":{
    "task_id":"xxx",
    "status":"pending"
  },
  "message":"success"
}
```

图片查询结果
```json
{
  "code":200,
  "message":"success",
  "data":{
    "task_id":"xxx",
    "task_type":"gpt_image_generation",
    "status":"completed",
    "result":{
      "images":["https://..."]
    },
    "created_at":"2026-05-13T13:48:01.000Z",
    "updated_at":"2026-05-13T13:48:35.000Z"
  }
}
```


## 约束与限制

- 异步接口，提交后需轮询获取结果
- 创建任务：POST `/images/generations`，返回 `task_id`
- 查询状态：GET `/images/tasks/{task_id}`
- 预计等待时间：30–120 秒
