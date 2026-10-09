# MiniMax 语音 2.8 HD

## 基本信息

| 属性 | 值 |
|------|-----|
| 任务类型 | audio (语音合成 / text-to-speech) |
| CFGPU 模型 ID | `MiniMax/speech-2.8-hd` |
| 任务（tasks） | text_to_speech, expressive_speech, pronunciation_control |
| 调用方式 | 同步（POST 直接返回结果） |
| 成本档位 | 2/5 |
| 速度档位 | 3/5 |

MiniMax 语音大模型能够根据上下文，智能预测文本的情绪、语调等信息，并生成超自然、高保真、个性化的语音。适用于社交、播客、有声书、新闻资讯、教育、数字人等场景。

## 价格

| 计费项 | 价格 |
|--------|------|
| 按字符数收费 | 0.3675 元 / 万字符 |

## 模型版本对比

| 模型 | CFGPU Model ID | 单价 | 特点 |
|------|----------------|------|------|
| MiniMax 语音 2.8 HD | `MiniMax/speech-2.8-hd` | 0.3675 元 / 万字符 | 高保真音质 |
| MiniMax 语音 2.8 Turbo | `MiniMax/speech-2.8-turbo` | 0.21 元 / 万字符 | 更快更省 |

## 参数说明

| 统一 Schema 字段 | MiniMax 字段 | 默认值 | 说明 |
|------------------|--------------|--------|------|
| text | input.text | - | 待合成文本（必填） |
| voice | input.voice_setting.voice_id | `male-qn-qingse` | 音色 ID |
| speed | input.voice_setting.speed | 1.0 | 语速 |
| volume | input.voice_setting.vol | 1.0 | 音量 |
| pitch | input.voice_setting.pitch | 0 | 音调 |
| emotion | input.voice_setting.emotion | （自动推断） | `happy` / `sad` / `angry` / `fearful` / `disgusted` / `surprised` / `calm` / `fluent` / `whisper` |
| sample_rate | input.audio_setting.sample_rate | 32000 | 采样率 |
| bitrate | input.audio_setting.bitrate | 128000 | 比特率 |
| audio_format | input.audio_setting.format | mp3 | 输出格式 |
| model_specific | （顶层合并） | - | 其他直传参数，如 pronunciation_dict、subtitle_enable |

> 文本中可内嵌情绪/事件标记，如 `今天是不是很开心呀(laughs)，当然了！`

`emotion` 的 9 个枚举依次用于控制：高兴、悲伤、愤怒、害怕、厌恶、惊讶、中性、生动、低语。

## 示例

### 同步语音创建

```json
{
  "model": "MiniMax/speech-2.8-hd",
  "input": {
    "text": "今天是不是很开心呀(laughs)，当然了！",
    "voice_setting": {
      "voice_id": "male-qn-qingse",
      "speed": 1,
      "vol": 1,
      "pitch": 0,
      "emotion": "happy"
    },
    "audio_setting": {
      "sample_rate": 32000,
      "bitrate": 128000,
      "format": "mp3",
      "channel": 1
    },
    "pronunciation_dict": {
      "tone": [
        "处理/(chu3)(li3)",
        "危险/dangerous"
      ]
    },
    "subtitle_enable": false
  }
}
```

响应结构

```json
{"output":
{"trace_id":"0693f8fe57c5b4a1abf4e029d5424532",
"extra_info":{"audio_size":66804,"word_count":22,"usage_characters":34,
"invisible_character_ratio":0,"audio_channel":1,"audio_length":4067,"audio_format":"mp3",
"bitrate":128000,"audio_sample_rate":32000},"base_resp":{"status_code":0,"status_msg":"success"},
"data":{"ced":"","audio":" 一段MPEG Layer III编码","status":2}},"usage":{"characters":34},"request_id":"41815a9b-3cf4-9f6a-a2c3-cdc2b7cf008f"}
```

> `pronunciation_dict` 与 `subtitle_enable` 可通过 `model_specific` 传入。

## 系统音色列表（voice 可选值 / Voice ID）

`voice` 参数映射到 `input.voice_setting.voice_id`，默认 `male-qn-qingse`。MiniMax 语音 2.8 Turbo 与 HD 使用同一份音色表。

可选音色请调用 `list_voice_profiles(model_ids=[...])` 查询：可按语言、性别、年龄段和关键词筛选，每条附带音色描述与适用场景。只能原样使用返回的 `voice` 字段，`label` 仅为展示名称。音色 id 的空格、全角括号和大小写都必须逐字照抄。

音色数据由平台的 `model_audio_voices` 导出生成，存放在本模型目录的 `voices.yaml`，这里不再重复列出。
