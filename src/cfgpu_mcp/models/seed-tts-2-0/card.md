# 豆包语音合成 2.0 (Seed-TTS 2.0)

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | audio (语音合成 / text-to-speech) |
| CFGPU 模型 ID | `seed-tts-2.0` |
| 任务（tasks） | text_to_speech, pronunciation_control |
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
| text | req_params.text | 待合成文本（必填），最多 10 万字符 |
| voice | req_params.speaker | 音色 ID，默认 `zh_female_xiaohe_uranus_bigtts` |
| audio_format | req_params.audio_params.format | `mp3`（默认）/ `pcm` / `ogg_opus`；不支持 `wav` / `flac`。除非用户明确要求，保持 `mp3` |
| sample_rate | req_params.audio_params.sample_rate | 8000 / 16000 / 22050 / 24000（默认）/ 32000 / 44100 / 48000；`ogg_opus` 仅 48000（缺省即 48000） |
| bitrate | req_params.audio_params.bit_rate | `mp3` / `ogg_opus`：64000（mp3 默认）或 160000；`pcm` 不支持 |
| speed | req_params.audio_params.speech_rate | 倍数 0.5–2.0，换算为 `(speed-1)*100`，即 [-50, 100] |
| volume | req_params.audio_params.loudness_rate | 倍数 0.5–2.0，换算同上 |
| pitch | req_params.additions.post_process.pitch | 半音 -12–12，默认 0 |
| emotion | — | 不支持，传入会被拒绝 |
| model_specific | 按 payload 形状逐层合并 | 见下 |

取默认值的 speed / volume / pitch / bitrate 不会写入 payload。

### model_specific：其余上游字段

`model_specific` 按 payload 形状写，`req_params`、`audio_params`、`additions` 逐层合并，只写要改的字段即可，不会覆盖 `text` / `speaker`。`additions` 在上游是 **JSON 字符串**，这里写成对象（或已编码的字符串）均可，适配器负责序列化，并与 `pitch` 生成的 `post_process` 合并。

| 字段 | 位置 | 说明 |
|------|------|------|
| ssml | req_params.ssml | SSML 文本，仅中英文音色支持；需 `disable_markdown_filter=false` |
| enable_timestamp | req_params.audio_params | 返回字级时间戳 |
| explicit_language | req_params.additions | `zh-cn` / `en` / `es-mx` / `id` / `pt-br`；文本须含该语种 |
| pronunciation_dict | req_params.additions | `{"tone": ["北京/(bei3)(jing1)", "omg/oh my god"]}`；最多 5000 条，原词 ≤ 9 字符；与 SSML 二选一 |
| silence_duration | req_params.additions | 句末静音 ms，0–30000 |
| disable_markdown_filter / disable_emoji_filter | req_params.additions | 过滤 Markdown / Emoji |
| latex_parser | req_params.additions | `v2`，需 `disable_markdown_filter=true` |
| max_length_to_filter_parenthesis | req_params.additions | 过滤括号内文本的长度上限 |
| aigc_watermark | req_params.additions | 结尾添加 AIGC 节奏标识 |
| disable_default_bit_rate | req_params.additions | 置 `true` 后 bit_rate 可取 16000 / 32000（需经 `audio_params.bit_rate` 直接传） |

示例：`{"req_params": {"additions": {"explicit_language": "en", "silence_duration": 500}}}`

## 异步任务流程

1. **创建任务**：POST `/voice/generations`，返回 `task_id`
2. **查询状态**：GET `/voice/tasks/{task_id}`
3. **轮询等待**：任务 `running` 时持续查询
4. **获取结果**：任务完成后返回音频 URL（下载链接 1 小时内有效；音频在服务端保留 7 天，过期后重新查询即可拿到新链接）

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
| 单次文本长度 | ≤ 10 万字符；非法控制字符占比超过 10% 时上游拒绝 |
| 输出音频格式 | mp3（默认）/ pcm / ogg_opus |
| 音频链接有效期 | 1 小时（优先取上游 `urlExpireTime`）；音频保留 7 天 |

## 系统音色列表（speaker 可选值 / voice_type）

`voice` 参数映射到 `req_params.speaker`，默认 `zh_female_xiaohe_uranus_bigtts`。

可选音色请调用 `list_voice_profiles(model_ids=[...])` 查询：可按语言、性别、年龄段和关键词筛选，每条附带音色描述与适用场景。只能原样使用返回的 `voice` 字段，`label` 仅为展示名称。音色 id 的空格、全角括号和大小写都必须逐字照抄。

音色数据由平台的 `model_audio_voices` 导出生成，存放在本模型目录的 `voices.yaml`，这里不再重复列出。
