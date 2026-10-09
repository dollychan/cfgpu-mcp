# CF Image 2

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | image | 
| 任务（tasks） | text_to_image, image_to_image, multi_image_fusion |
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
- **多图融合**：一次传入多张参考图，按 prompt 组合其中的主体、场景或风格；全部参考图原样下发，上游数量上限未在文档中给出

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | string | 必填 | 图片描述 | 
| `aspect_ratio` | string | `1:1` | 1:1、3:2、2:3、4:3、3:4、16:9、9:16、21:9、9:21、3:1、1:3 |
| `resolution` | string | `1K` | 分辨率档位：`1K` / `2K` / `4K`，其他取值（含空串）上游报参数错误 |
| `quality` | string | `medium` | 生成质量：`low` / `medium` / `high` |
| `n` | integer | `1` | 单次最多返回的图片数量，范围 `1`–`10`；模型可能少于该值 |
| `reference_images` | list[url] | 可选 | 参考图 URL 数组，传入后进入图片编辑模式 |
| `model_specific` | dict | 可选 | 透传到 API 的额外参数 |

### 统一 Schema 映射

| 统一 Schema | API 字段 | 说明 |
|---|---|---|
| `resolution` | `resolution` | `1K` / `2K` / `4K` 原样下发——API 三档都是字面量，空串会被判参数错误（`resolution 参数必须为 '1K'、'2K' 或 '4K'`）；`3K` 无对应档位，原样下发由上游拒绝 |
| `aspect_ratio` | `aspect_ratio` | 原样下发。支持 1:1、3:2、2:3、4:3、3:4、16:9、9:16、21:9、9:21、3:1、1:3 |
| `quality_tier` | `quality` | `fast` → `low`、`balanced` → `medium`、`best` → `high`。**本模型的 `quality_tier` 不只是路由偏好，选定模型后仍然生效**（同可灵的 `quality_tier` → `mode`）；`model_specific={"quality": ...}` 最后合并，可覆盖该映射 |
| `n` | `n` | 原样下发，范围 `1`–`10`，代表最多输出张数；模型可能少于该值，超出范围会在本地校验阶段拒绝 |
| `watermark` | — | 不支持：静默忽略，其余参数照常发送 |

## 使用示例

**文生图**
```json
{
  "prompt": "一只可爱的猫咪，写实风格"
}
```

**图生图**
```json
{
  "prompt": "将图片风格改为水彩画", 
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
    "task_type":"cf_image_generation",
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
