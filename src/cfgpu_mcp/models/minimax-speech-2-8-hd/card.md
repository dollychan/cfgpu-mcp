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

CFGPU 用 `input` 信封转发 MiniMax T2A 接口，字段名沿用同步接口（`audio_setting.sample_rate`，不是异步接口的 `audio_sample_rate`）；取值范围以 `reference`（MiniMax 官方文档）为准。

| 统一 Schema 字段 | MiniMax 字段 | 默认值 | 取值 |
|------------------|--------------|--------|------|
| text | input.text | - | 必填；长度不做本地校验，超长由上游拒绝 |
| voice | input.voice_setting.voice_id | `male-qn-qingse` | 系统音色 id（见下方音色列表） |
| speed | input.voice_setting.speed | 1.0 | [0.5, 2] |
| volume | input.voice_setting.vol | 1.0 | (0, 10] |
| pitch | input.voice_setting.pitch | 0 | [-12, 12] 整数，半音 |
| emotion | input.voice_setting.emotion | （自动推断） | `happy` / `sad` / `angry` / `fearful` / `disgusted` / `surprised` / `calm` / `fluent`；**2.8 不支持 `whisper`** |
| audio_format | input.audio_setting.format | mp3 | `mp3` / `wav` / `flac` / `pcm` / `ogg_opus`（上游写作 `opus`）。除非用户明确要求，保持 `mp3` |
| sample_rate | input.audio_setting.sample_rate | 32000 | 8000 / 16000 / 22050 / 24000 / 32000 / 44100；`ogg_opus` 只能取 8000 / 16000 / 24000（缺省 24000）。文档列出的 12000 / 48000 会被中转按通用集合拒绝（`invalid params: sample_rate`），32000 能过参数校验却在上游以不透明的 400 失败且仍回报 usage。opus + 24000（不带 bitrate）已实测可用，返回完整的 Ogg/Opus 文件 |
| bitrate | input.audio_setting.bitrate | 128000 | 32000 / 64000 / 128000 / 256000，**仅 mp3**：其他格式不发送，显式传入会被拒绝 |
| — | input.audio_setting.channel | 1 | 固定发单声道（上游默认 2），可经 model_specific 改 |
| model_specific | 按 payload 形状逐层合并 | - | 见下 |

`pcm` 的产物是无文件头的 16-bit 小端裸采样，`inline_media` 项附带 `sample_format` / `sample_rate` / `channels`，宿主需自行封装（如加 WAV 头）才能播放。

越界的 speed / volume / pitch / emotion 在本地拒绝，不会被夹到边界；`validate_only` 只把格式、采样率、码率修正到最近的合法值并写进 `corrected_args`。

`emotion` 的 8 个取值依次为：高兴、悲伤、愤怒、害怕、厌恶、惊讶、中性、生动。模型会按文本自动匹配情绪，一般无需指定。

### 语气词标签

2.8 HD / Turbo 支持在 `text` 中插入语气词标签：`(laughs)` 笑声、`(chuckle)` 轻笑、`(coughs)` 咳嗽、`(clear-throat)` 清嗓子、`(groans)` 呻吟、`(breath)` 换气、`(pant)` 喘气、`(inhale)` 吸气、`(exhale)` 呼气、`(gasps)` 倒吸气、`(sniffs)` 吸鼻子、`(sighs)` 叹气、`(snorts)` 喷鼻息、`(burps)` 打嗝、`(lip-smacking)` 咂嘴、`(humming)` 哼唱、`(hissing)` 嘶嘶声、`(emm)` 嗯、`(whistles)` 口哨、`(sneezes)` 喷嚏、`(crying)` 抽泣、`(applause)` 鼓掌。

### model_specific：其余上游字段

`model_specific` 按 payload 形状写：`input` 逐键合并，其中 `voice_setting` / `audio_setting` / `voice_modify` 再深入一层合并，只写要改的字段，不会覆盖 `text` 与类型化字段。

| 字段 | 位置 | 说明 |
|------|------|------|
| pronunciation_dict | input | `{"tone": ["燕少飞/(yan4)(shao3)(fei1)", "omg/oh my god"]}`；声调用 1–5 表示（5 为轻声） |
| language_boost | input | 增强小语种 / 方言识别：`auto`、`Chinese`、`Chinese,Yue`、`English`、`Japanese` 等 40 种 |
| english_normalization | input.voice_setting | 英文数字规范化，略增时延 |
| channel | input.audio_setting | 1 / 2 |
| voice_modify | input | 声音效果器：`pitch` / `intensity` / `timbre` 各 [-100, 100]，`sound_effects` ∈ `spacious_echo` / `auditorium_echo` / `lofi_telephone` / `robotic`；**仅 mp3 / wav / flac**，pcm 与 opus 会被上游拒绝 |
| aigc_watermark | input | 在音频末尾添加 AIGC 节奏标识 |
| subtitle_enable | input | 返回字幕时间信息 |
| format: `pcmu_raw` / `pcmu_wav` | input.audio_setting | G.711 μ-law（8 kHz），统一 schema 无对应取值，只能经此传入 |

示例：`{"input": {"language_boost": "auto", "voice_modify": {"sound_effects": "spacious_echo"}}}`

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

> `pronunciation_dict`、`voice_modify`、`language_boost` 等字段通过 `model_specific` 传入，见上表。

## 系统音色列表（voice 可选值 / Voice ID）

`voice` 参数映射到 `input.voice_setting.voice_id`，默认 `male-qn-qingse`。MiniMax 语音 2.8 Turbo 与 HD 使用同一份音色表。

可选音色请调用 `list_voice_profiles(model_ids=[...])` 查询：可按语言、性别、年龄段和关键词筛选，每条附带音色描述与适用场景。只能原样使用返回的 `voice` 字段，`label` 仅为展示名称。音色 id 的空格、全角括号和大小写都必须逐字照抄。

音色数据由平台的 `model_audio_voices` 导出生成，存放在本模型目录的 `voices.yaml`，这里不再重复列出。
