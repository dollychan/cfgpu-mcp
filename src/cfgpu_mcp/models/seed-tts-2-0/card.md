# 豆包语音合成 2.0 (Seed-TTS 2.0)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | audio (语音合成 / text-to-speech) |
| CFGPU 模型 ID | `seed-tts-2.0` |
| 能力标签 | text_to_speech |
| 调用方式 | 异步（提交后轮询查询） |
| 成本档位 | 3/5 |
| 速度档位 | 3/5 |

## 价格

| 计费项 | 价格 |
|--------|------|
| 按字符数收费 | 2.94 元 / 万字符 |

## 参数说明

| 统一 Schema 字段 | seed-tts 字段 | 说明 |
|------------------|---------------|------|
| text | req_params.text | 待合成文本（必填） |
| voice | req_params.speaker | 音色 ID，默认 `zh_female_xiaohe_uranus_bigtts` |
| audio_format | req_params.audio_params.format | 输出格式，默认 `mp3` |
| sample_rate | req_params.audio_params.sample_rate | 采样率，默认 `24000` |
| model_specific | （顶层合并） | 其他直传参数，如 callback_url |

> `speed` / `volume` / `pitch` / `emotion` 为 MiniMax 专用参数，seed-tts-2.0 不使用。

## 异步任务流程

1. **创建任务**：POST `/voice/generations`，返回 `task_id`
2. **查询状态**：GET `/voice/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务完成后返回音频 URL（24 小时内有效）

## 示例

### 语音创建

```json
{
  "model": "seed-tts-2.0",
  "req_params": {
    "text": "明朝开国皇帝朱元璋也称这本书为，万物之根",
    "speaker": "zh_female_xiaohe_uranus_bigtts",
    "audio_params": {
      "format": "mp3",
      "sample_rate": 24000
    },
    "callback_url": ""
  }
}
```

响应结构

```json
{"code":20000000,
"data":{
  "task_status":1,
  "req_text_length":20,
  "task_id":"be2d0ed3-cad6-47a6-bed6-8e9fe247ab69"},
  "message":"ok"}% 
```


### 语音查询

```
GET /voice/tasks/{task_id}
```

响应结构

```json
{"code":20000000,
"message":"ok",
"data":{"taskId":"be2d0ed3-cad6-47a6-bed6-8e9fe247ab69",
"taskStatus":2,
"audioUrl":"https://...",
"reqTextLength":20,
"synthesizeTextLength":20,
"urlExpireTime":1782895148},
"running":false,
"success":true,
"failure":false}
```

查询结果中的 `data.synthesizeTextLength` 是实际合成的计费字符数。adapter 会将其
转换为统一结果字段；如果该字段缺失，则回退读取 `data.reqTextLength`，两者均缺失时
不生成 usage：

```json
{"usage":{"characters":20}}
```

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 输出音频格式 | mp3（默认） |
| 音频链接有效期 | 24 小时 |

## 系统音色列表（speaker 可选值 / voice_type）

`voice` 参数映射到 `req_params.speaker`，默认 `zh_female_xiaohe_uranus_bigtts`。

可选音色请调用 `list_voice_profiles(model_ids=[...])` 查询：可按语言、性别、年龄段和关键词筛选，每条附带音色描述与适用场景。只能原样使用返回的 `voice` 字段，`label` 仅为展示名称。音色 id 的空格、全角括号和大小写都必须逐字照抄。

音色数据由平台的 `model_audio_voices` 导出生成，存放在本模型目录的 `voices.yaml`，这里不再重复列出。
