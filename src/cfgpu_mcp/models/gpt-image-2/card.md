# GPT Image 2

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | image |
| CFGPU 模型 ID | `gpt-image-2` |
| 成本档位 | 2/5 |
| 速度档位 | 3/5 |
| 价格 | 0.105 元/张（文生图 / 图生图） |

## 能力

- **文生图**：文本描述生成图片
- **图生图**：参考图 + 文本提示编辑或变换图片

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | string | 必填 | 图片描述 |
| `model` | string | - | 使用 `gpt-image-2` |
| `aspect_ratio` | string | `1:1` | 1:1、3:2、2:3、4:3、3:4、16:9、9:16 |
| `reference_images` | list[url] | 可选 | 参考图 URL 数组，传入后进入图片编辑模式 |
| `model_specific` | dict | 可选 | 透传到 API 的额外参数 |

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

## 约束与限制

- 异步接口，提交后需轮询获取结果
- 创建任务：POST `/images/generations`，返回 `task_id`
- 查询状态：GET `/images/tasks/{task_id}`
- 预计等待时间：30–120 秒
