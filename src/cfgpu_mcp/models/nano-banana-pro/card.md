# Nano Banana Pro

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | image |
| CFGPU 模型 ID | `nano-pro` |
| 成本档位 | 3/5 |
| 速度档位 | 3/5 |
| 价格 | 0.21 元/张（文生图 / 图生图） |

## 能力

- **文生图**：文本描述生成图片
- **图生图**：参考图 + 文本提示编辑或变换图片

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | string | 必填 | 图片描述 |
| `model` | string | - | 使用 `nano-banana-pro` |
| `resolution` | string | `2K` | 图片分辨率：1K（默认）、2K、4K（映射为 image_size） |
| `aspect_ratio` | string | `1:1` | 1:1、3:4、4:3、9:16、16:9、21:9 |
| `reference_images` | list[url] | 可选 | 参考图 URL 数组 |
| `model_specific` | dict | 可选 | 透传到 API 的额外参数 |

## 使用示例

**文生图**
```json
{
  "prompt": "一只可爱的猫咪，写实风格",
  "model": "nano-banana-pro"
}
```

## 约束与限制

- 异步接口，提交后需轮询获取结果
- 创建任务：POST `/images/generations`，返回 `task_id`
- 查询状态：GET `/images/tasks/{task_id}`
- 预计等待时间：30–120 秒
