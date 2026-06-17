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

### 语音查询

```
GET /voice/tasks/{task_id}
```

## 约束与限制

| 限制项 | 值 |
|--------|-----|
| 输出音频格式 | mp3（默认） |
| 音频链接有效期 | 24 小时 |

## 系统音色列表（speaker 可选值 / voice_type）

`voice` 参数映射到 `req_params.speaker`，默认 `zh_female_xiaohe_uranus_bigtts`。
除特别标注外，`*_uranus_bigtts` 音色均支持「情感变化、指令遵循、ASMR」；`saturn_*_tob` 音色支持「指令遵循、COT/QA 功能」；`saturn_*_cs_tob` 客服音色支持「指令遵循」。
来源：豆包语音合成 2.0 官方音色表。

| 场景 | 名称 | speaker (voice_type) | 语种/方言 | 标签 |
|------|------|----------------------|-----------|------|
| 通用场景 | Vivi 2.0 | `zh_female_vv_uranus_bigtts` | 中文、日文、印尼、墨西哥西班牙语；方言：四川、陕西、东北 | |
| 通用场景 | 小何 2.0 | `zh_female_xiaohe_uranus_bigtts` | 中文 | 默认 |
| 通用场景 | 云舟 2.0 | `zh_male_m191_uranus_bigtts` | 中文 | |
| 通用场景 | 小天 2.0 | `zh_male_taocheng_uranus_bigtts` | 中文 | |
| 通用场景 | 刘飞 2.0 | `zh_male_liufei_uranus_bigtts` | 中文 | |
| 通用场景 | 魅力苏菲 2.0 | `zh_female_sophie_uranus_bigtts` | 中文 | |
| 通用场景 | 清新女声 2.0 | `zh_female_qingxinnvsheng_uranus_bigtts` | 中文 | |
| 角色扮演 | 知性灿灿 2.0 | `zh_female_cancan_uranus_bigtts` | 中文 | |
| 角色扮演 | 撒娇学妹 2.0 | `zh_female_sajiaoxuemei_uranus_bigtts` | 中文 | |
| 通用场景 | 甜美小源 2.0 | `zh_female_tianmeixiaoyuan_uranus_bigtts` | 中文 | |
| 通用场景 | 甜美桃子 2.0 | `zh_female_tianmeitaozi_uranus_bigtts` | 中文 | |
| 通用场景 | 爽快思思 2.0 | `zh_female_shuangkuaisisi_uranus_bigtts` | 中文 | |
| 视频配音 | 佩奇猪 2.0 | `zh_female_peiqi_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 通用场景 | 邻家女孩 2.0 | `zh_female_linjianvhai_uranus_bigtts` | 中文 | |
| 通用场景 | 少年梓辛/Brayan 2.0 | `zh_male_shaonianzixin_uranus_bigtts` | 中文 | |
| 视频配音 | 猴哥 2.0 | `zh_male_sunwukong_uranus_bigtts` | 中文 | |
| 教育场景 | Tina老师 2.0 | `zh_female_yingyujiaoxue_uranus_bigtts` | 中文、英式英语 | |
| 客服场景 | 暖阳女声 2.0 | `zh_female_kefunvsheng_uranus_bigtts` | 中文 | |
| 有声阅读 | 儿童绘本 2.0 | `zh_female_xiaoxue_uranus_bigtts` | 中文 | |
| 视频配音 | 大壹 2.0 | `zh_male_dayi_uranus_bigtts` | 中文 | |
| 视频配音 | 黑猫侦探社咪仔 2.0 | `zh_female_mizai_uranus_bigtts` | 中文 | |
| 视频配音 | 鸡汤女 2.0 | `zh_female_jitangnv_uranus_bigtts` | 中文 | |
| 通用场景 | 魅力女友 2.0 | `zh_female_meilinvyou_uranus_bigtts` | 中文 | |
| 视频配音 | 流畅女声 2.0 | `zh_female_liuchangnv_uranus_bigtts` | 中文 | |
| 视频配音 | 儒雅逸辰 2.0 | `zh_male_ruyayichen_uranus_bigtts` | 中文 | |
| 多语种 | Tim | `en_male_tim_uranus_bigtts` | 美式英语 | |
| 多语种 | Dacey | `en_female_dacey_uranus_bigtts` | 美式英语 | |
| 多语种 | Stokie | `en_female_stokie_uranus_bigtts` | 美式英语 | |
| 通用场景 | 温柔妈妈 2.0 | `zh_female_wenroumama_uranus_bigtts` | 中文 | |
| 通用场景 | 解说小明 2.0 | `zh_male_jieshuoxiaoming_uranus_bigtts` | 中文 | |
| 通用场景 | TVB女声 2.0 | `zh_female_tvbnv_uranus_bigtts` | 中文 | |
| 通用场景 | 译制片男 2.0 | `zh_male_yizhipiannan_uranus_bigtts` | 中文 | |
| 通用场景 | 俏皮女声 2.0 | `zh_female_qiaopinv_uranus_bigtts` | 中文 | |
| 角色扮演 | 直率英子 2.0 | `zh_female_zhishuaiyingzi_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 通用场景 | 邻家男孩 2.0 | `zh_male_linjiananhai_uranus_bigtts` | 中文 | |
| 角色扮演 | 四郎 2.0 | `zh_male_silang_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 通用场景 | 儒雅青年 2.0 | `zh_male_ruyaqingnian_uranus_bigtts` | 中文 | 番茄小说/豆包/剪映同款 |
| 角色扮演 | 擎苍 2.0 | `zh_male_qingcang_uranus_bigtts` | 中文 | 番茄小说/豆包/抖音/剪映同款 |
| 角色扮演 | 熊二 2.0 | `zh_male_xionger_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 角色扮演 | 樱桃丸子 2.0 | `zh_female_yingtaowanzi_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 通用场景 | 温暖阿虎/Alvin 2.0 | `zh_male_wennuanahu_uranus_bigtts` | 中文 | |
| 通用场景 | 奶气萌娃 2.0 | `zh_male_naiqimengwa_uranus_bigtts` | 中文 | 剪映/豆包同款 |
| 通用场景 | 婆婆 2.0 | `zh_female_popo_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 通用场景 | 高冷御姐 2.0 | `zh_female_gaolengyujie_uranus_bigtts` | 中文 | |
| 通用场景 | 傲娇霸总 2.0 | `zh_male_aojiaobazong_uranus_bigtts` | 中文 | |
| 角色扮演 | 懒音绵宝 2.0 | `zh_male_lanyinmianbao_uranus_bigtts` | 中文 | |
| 通用场景 | 反卷青年 2.0 | `zh_male_fanjuanqingnian_uranus_bigtts` | 中文 | |
| 通用场景 | 温柔淑女 2.0 | `zh_female_wenroushunv_uranus_bigtts` | 中文 | 番茄小说/豆包/剪映同款 |
| 角色扮演 | 古风少御 2.0 | `zh_female_gufengshaoyu_uranus_bigtts` | 中文 | |
| 通用场景 | 活力小哥 2.0 | `zh_male_huolixiaoge_uranus_bigtts` | 中文 | |
| 有声阅读 | 霸气青叔 2.0 | `zh_male_baqiqingshu_uranus_bigtts` | 中文 | 番茄小说/豆包/剪映同款 |
| 有声阅读 | 悬疑解说 2.0 | `zh_male_xuanyijieshuo_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 通用场景 | 萌丫头/Cutey 2.0 | `zh_female_mengyatou_uranus_bigtts` | 中文 | |
| 通用场景 | 贴心女声/Candy 2.0 | `zh_female_tiexinnvsheng_uranus_bigtts` | 中文 | |
| 通用场景 | 鸡汤妹妹/Hope 2.0 | `zh_female_jitangmei_uranus_bigtts` | 中文 | 抖音/豆包同款 |
| 通用场景 | 磁性解说男声/Morgan 2.0 | `zh_male_cixingjieshuonan_uranus_bigtts` | 中文 | 抖音/剪映同款 |
| 通用场景 | 亮嗓萌仔 2.0 | `zh_male_liangsangmengzai_uranus_bigtts` | 中文 | |
| 通用场景 | 开朗姐姐 2.0 | `zh_female_kailangjiejie_uranus_bigtts` | 中文 | |
| 通用场景 | 高冷沉稳 2.0 | `zh_male_gaolengchenwen_uranus_bigtts` | 中文 | 猫箱同款 |
| 通用场景 | 深夜播客 2.0 | `zh_male_shenyeboke_uranus_bigtts` | 中文 | |
| 角色扮演 | 鲁班七号 2.0 | `zh_male_lubanqihao_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 通用场景 | 娇喘女声 2.0 | `zh_female_jiaochuannv_uranus_bigtts` | 中文 | 抖音/剪映同款 |
| 角色扮演 | 林潇 2.0 | `zh_female_linxiao_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 角色扮演 | 玲玲姐姐 2.0 | `zh_female_lingling_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 角色扮演 | 春日部姐姐 2.0 | `zh_female_chunribu_uranus_bigtts` | 中文 | 抖音/豆包/剪映同款 |
| 角色扮演 | 唐僧 2.0 | `zh_male_tangseng_uranus_bigtts` | 中文 | 抖音/豆包同款 |
| 角色扮演 | 庄周 2.0 | `zh_male_zhuangzhou_uranus_bigtts` | 中文 | 抖音/剪映同款 |
| 通用场景 | 开朗弟弟 2.0 | `zh_male_kailangdidi_uranus_bigtts` | 中文 | 抖音/剪映同款 |
| 角色扮演 | 猪八戒 2.0 | `zh_male_zhubajie_uranus_bigtts` | 中文 | 豆包/剪映同款 |
| 角色扮演 | 感冒电音姐姐 2.0 | `zh_female_ganmaodianyin_uranus_bigtts` | 中文 | 抖音/剪映同款 |
| 通用场景 | 谄媚女声 2.0 | `zh_female_chanmeinv_uranus_bigtts` | 中文 | 抖音/剪映同款 |
| 角色扮演 | 女雷神 2.0 | `zh_female_nvleishen_uranus_bigtts` | 中文 | 剪映/豆包同款 |
| 通用场景 | 亲切女声 2.0 | `zh_female_qinqienv_uranus_bigtts` | 中文 | 豆包同款 |
| 通用场景 | 快乐小东 2.0 | `zh_male_kuailexiaodong_uranus_bigtts` | 中文 | 豆包同款 |
| 通用场景 | 开朗学长 2.0 | `zh_male_kailangxuezhang_uranus_bigtts` | 中文 | 豆包同款 |
| 通用场景 | 悠悠君子 2.0 | `zh_male_youyoujunzi_uranus_bigtts` | 中文 | 豆包同款 |
| 通用场景 | 文静毛毛 2.0 | `zh_female_wenjingmaomao_uranus_bigtts` | 中文 | 豆包同款 |
| 通用场景 | 知性女声 2.0 | `zh_female_zhixingnv_uranus_bigtts` | 中文 | |
| 通用场景 | 清爽男大 2.0 | `zh_male_qingshuangnanda_uranus_bigtts` | 中文 | 豆包同款 |
| 通用场景 | 渊博小叔 2.0 | `zh_male_yuanboxiaoshu_uranus_bigtts` | 中文 | |
| 通用场景 | 阳光青年 2.0 | `zh_male_yangguangqingnian_uranus_bigtts` | 中文 | |
| 通用场景 | 清澈梓梓 2.0 | `zh_female_qingchezizi_uranus_bigtts` | 中文 | |
| 通用场景 | 甜美悦悦 2.0 | `zh_female_tianmeiyueyue_uranus_bigtts` | 中文 | |
| 通用场景 | 心灵鸡汤 2.0 | `zh_female_xinlingjitang_uranus_bigtts` | 中文 | |
| 通用场景 | 温柔小哥 2.0 | `zh_male_wenrouxiaoge_uranus_bigtts` | 中文 | |
| 通用场景 | 柔美女友 2.0 | `zh_female_roumeinvyou_uranus_bigtts` | 中文 | |
| 通用场景 | 东方浩然 2.0 | `zh_male_dongfanghaoran_uranus_bigtts` | 中文 | |
| 通用场景 | 温柔小雅 2.0 | `zh_female_wenrouxiaoya_uranus_bigtts` | 中文 | |
| 通用场景 | 天才童声 2.0 | `zh_male_tiancaitongsheng_uranus_bigtts` | 中文 | |
| 角色扮演 | 武则天 2.0 | `zh_female_wuzetian_uranus_bigtts` | 中文 | 剪映同款 |
| 角色扮演 | 顾姐 2.0 | `zh_female_gujie_uranus_bigtts` | 中文 | 抖音/剪映同款 |
| 通用场景 | 广告解说 2.0 | `zh_male_guanggaojieshuo_uranus_bigtts` | 中文 | 剪映同款 |
| 有声阅读 | 少儿故事 2.0 | `zh_female_shaoergushi_uranus_bigtts` | 中文 | |
| 角色扮演 | 调皮公主 | `saturn_zh_female_tiaopigongzhu_tob` | 中文 | COT/QA |
| 角色扮演 | 爽朗少年 | `saturn_zh_male_shuanglangshaonian_tob` | 中文 | COT/QA |
| 角色扮演 | 天才同桌 | `saturn_zh_male_tiancaitongzhuo_tob` | 中文 | COT/QA |
| 角色扮演 | 知性灿灿 | `saturn_zh_female_cancan_tob` | 中文 | COT/QA |
| 角色扮演 | 傲娇女友 2.0 | `saturn_zh_female_aojiaonvyou_tob` | 中文 | COT/QA |
| 角色扮演 | 病娇姐姐 2.0 | `saturn_zh_female_bingjiaojiejie_tob` | 中文 | COT/QA |
| 角色扮演 | 成熟姐姐 2.0 | `saturn_zh_female_chengshujiejie_tob` | 中文 | COT/QA |
| 角色扮演 | 可爱女生 2.0 | `saturn_zh_female_keainvsheng_tob` | 中文 | COT/QA |
| 角色扮演 | 暖心学姐 2.0 | `saturn_zh_female_nuanxinxuejie_tob` | 中文 | COT/QA |
| 角色扮演 | 贴心女友 2.0 | `saturn_zh_female_tiexinnvyou_tob` | 中文 | COT/QA |
| 通用场景 | 温柔文雅 2.0 | `saturn_zh_female_wenrouwenya_tob` | 中文 | COT/QA |
| 角色扮演 | 妩媚御姐 2.0 | `saturn_zh_female_wumeiyujie_tob` | 中文 | COT/QA |
| 角色扮演 | 性感御姐 2.0 | `saturn_zh_female_xingganyujie_tob` | 中文 | COT/QA |
| 角色扮演 | 傲气凌人 2.0 | `saturn_zh_male_aiqilingren_tob` | 中文 | COT/QA |
| 角色扮演 | 傲娇公子 2.0 | `saturn_zh_male_aojiaogongzi_tob` | 中文 | COT/QA |
| 角色扮演 | 傲娇精英 2.0 | `saturn_zh_male_aojiaojingying_tob` | 中文 | COT/QA |
| 角色扮演 | 傲慢少爷 2.0 | `saturn_zh_male_aomanshaoye_tob` | 中文 | COT/QA |
| 角色扮演 | 霸道少爷 2.0 | `saturn_zh_male_badaoshaoye_tob` | 中文 | COT/QA |
| 角色扮演 | 病娇白莲 2.0 | `saturn_zh_male_bingjiaobailian_tob` | 中文 | COT/QA |
| 角色扮演 | 不羁青年 2.0 | `saturn_zh_male_bujiqingnian_tob` | 中文 | COT/QA |
| 角色扮演 | 成熟总裁 2.0 | `saturn_zh_male_chengshuzongcai_tob` | 中文 | COT/QA |
| 角色扮演 | 磁性男嗓 2.0 | `saturn_zh_male_cixingnansang_tob` | 中文 | COT/QA |
| 角色扮演 | 醋精男友 2.0 | `saturn_zh_male_cujingnanyou_tob` | 中文 | COT/QA |
| 角色扮演 | 风发少年 2.0 | `saturn_zh_male_fengfashaonian_tob` | 中文 | COT/QA |
| 角色扮演 | 腹黑公子 2.0 | `saturn_zh_male_fuheigongzi_tob` | 中文 | COT/QA |
| 客服场景 | 轻盈朵朵 2.0 | `saturn_zh_female_qingyingduoduo_cs_tob` | 中文 | 客服 |
| 客服场景 | 温婉珊珊 2.0 | `saturn_zh_female_wenwanshanshan_cs_tob` | 中文 | 客服 |
| 客服场景 | 热情艾娜 2.0 | `saturn_zh_female_reqingaina_cs_tob` | 中文 | 客服 |
| 客服场景 | 清新沐沐 2.0 | `saturn_zh_male_qingxinmumu_cs_tob` | 中文 | 客服 |
