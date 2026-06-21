# 日语视频转中文字幕工具 — 实现规格

> 给 AI 开发代理的构建规格。已固定技术决策,按本文件直接实现。
> 平台:Windows ｜ 形态:Tauri 桌面应用(Rust 壳 + React 前端 + Python 边车)

---

## 1. 技术栈(已固定,不要替换)

| 层 | 技术 | 版本/说明 |
|---|---|---|
| 桌面壳 | Tauri 最新稳定版(2.9.x,Rust) | 窗口、文件对话框、sidecar 管理、GPU 探测、密钥存储 |
| 前端 | React + TypeScript + Vite | UI、任务面板、字幕编辑器 |
| 引擎 | Python 3.12,PyInstaller 冻结为 sidecar | 全部重计算逻辑;3.12 的 ML 依赖 wheel 最齐全,不建议上 3.13/3.14 |
| 前后端通信 | 本地回环 HTTP(FastAPI)+ SSE 进度流 | 仅绑 127.0.0.1,随机端口,经 Tauri 注入前端 |
| 识别 | faster-whisper(CTranslate2) | 自适应 GPU/CPU |
| 媒体 | ffmpeg | 随包分发 |
| 翻译 | 本地 LLM(Ollama/llama.cpp)+ 在线 API,策略模式 | 运行时可切换 |
| 配置 | SQLite + Windows 凭据管理器(存 API key) | |

**进程模型**:Tauri 主进程拉起 Python sidecar;sidecar 崩溃由 Rust 健康检查后重启。

---

## 2. 目录结构

```
/shell        Rust(Tauri):窗口、对话框、GPU探测、sidecar生命周期、密钥、事件转发
/ui           React/TS:导入、设置、进度、字幕编辑、导出
/engine
  /server     FastAPI:任务接口 + SSE
  /media      ffmpeg 抽音轨/转码/烧录
  /asr        faster-whisper 自适应识别
  /translate  双引擎策略 + 术语表 + 上下文批译
  /subtitle   SRT/ASS 序列化、双语、合并拆分
  /orchestrator 任务状态机、进度、取消、错误归类
```

**开发顺序**:先让 `/engine` 命令行跑通「视频→中文SRT」,再接壳与 UI,最后打包。

---

## 3. 数据结构

```
Segment {
  index:    int
  start:    float    // 秒
  end:      float
  text_src: string   // 日文原文
  text_zh:  string   // 中文译文,翻译后填充
}

Task {
  id:       string
  input:    path
  stage:    enum(pending|extracting|transcribing|translating|muxing|done|failed|canceled)
  progress: float     // 0..1
  options:  TaskOptions
  error?:   { kind, message, hint }
}

TaskOptions {
  model:         string   // tiny|base|small|medium|large-v3,默认见 §5
  device:        string   // cuda|cpu,默认自适应
  engine:        string   // "local" | "online"
  engine_params: object   // local: {model_name}; online: {provider, api_key_ref}
  output_format: string   // "srt" | "ass"
  bilingual:     bool      // 双语字幕
  burn_in:       bool      // 烧录硬字幕
  glossary:      [{src, dst}]  // 术语表
}
```

---

## 4. 引擎 HTTP 接口(FastAPI)

```
POST /tasks                 // body: {input_path, options} -> {task_id}
GET  /tasks/{id}            // -> Task
POST /tasks/{id}/cancel     // -> 204
GET  /tasks/{id}/events     // SSE: 推送 {stage, progress, message}
GET  /tasks/{id}/result     // -> {srt_path?, ass_path?, video_path?}
GET  /tasks/{id}/segments   // -> [Segment]    (供前端字幕编辑器)
PUT  /tasks/{id}/segments   // body: [Segment] // 校对后回写,重新导出
GET  /capabilities          // -> {gpu: bool, cuda_version?, models_cached: [...]}
GET  /health                // -> 200 (Rust 健康检查用)
```

进度权重(orchestrator):extracting 0.05 / transcribing 0.55 / translating 0.35 / muxing 0.05。阶段内按音频时长或分段数推细粒度百分比。

---

## 5. 识别模块(/asr)

自适应逻辑:

```python
from faster_whisper import WhisperModel

if cuda_available:
    device, compute_type, default_model = "cuda", "float16", "large-v3"
else:
    device, compute_type, default_model = "cpu", "int8", "medium"
# options.model 若指定则覆盖 default_model

model = WhisperModel(options.model or default_model, device=device, compute_type=compute_type)
segments, info = model.transcribe(
    audio_wav, language="ja", task="transcribe", vad_filter=True
)
```

- `language="ja"` 显式固定,`task="transcribe"`(不要用 whisper 自带 translate,它只译英文)
- 抽音轨标准:16kHz 单声道 wav — `ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 -f wav audio.wav`
- 逐段产出时回传进度
- cuda 探测在 Rust 侧做,结果经 `/capabilities` 或启动参数传入

---

## 6. 翻译模块(/translate)

策略模式,统一接口:

```python
class TranslatorInterface:
    def translate(self, segments: list[Segment], options) -> list[Segment]: ...

class LocalLLMEngine(TranslatorInterface):   # Ollama/llama.cpp,离线
class OnlineApiEngine(TranslatorInterface):  # provider: deepl|google|openai,需 api_key
```

实现要求:

- **上下文批译**:分批送入(每批含前后文),不要逐句孤立翻译
- **术语表**:翻译前注入 options.glossary(人名/作品名对照)
- **时间轴只读**:只填 text_zh,绝不改 start/end
- 在线引擎:并发限制 + 指数退避重试,失败分段可单独重试
- 在线失败可降级提示切本地引擎重跑剩余分段

---

## 7. 字幕模块(/subtitle)

- 导出 SRT 与 ASS;ASS 支持双语(同段叠日文原文 + 中文译文)
- 时间格式化、文本转义、过长行折行、相邻短段可合并
- 烧录调 ffmpeg:软字幕封装 `mov_text`/`ass`,硬字幕用 `subtitles` 滤镜烧入画面

---

## 8. Tauri 壳职责(/shell)

- 文件对话框取视频本地绝对路径
- 启动/监控/重启 Python sidecar,把随机端口注入前端
- GPU 探测(CUDA 是否可用),结果传引擎
- API key 经 Windows 凭据管理器存取(不明文落盘)
- 把 SSE 进度事件转发给前端

---

## 9. 前端职责(/ui)

视图:导入区(拖拽视频、显示算力与耗时预估)、设置区(模型档/引擎/key/输出格式/烧录/术语表)、进度面板(阶段进度+可取消)、**字幕编辑器(逐条改文本/调时间/合并拆分)**、导出。

前端不含重逻辑,只发任务、订阅 SSE、渲染结果、做字幕校对。字幕编辑器是机翻质量的关键补偿,必须实现。

---

## 10. 打包

```
PyInstaller  -> engine.exe(含 faster-whisper 等),声明为 Tauri sidecar
Vite build   -> ui/dist
ffmpeg.exe   -> 作为 Tauri resource 随包
Tauri build  -> .msi 或 NSIS 安装包
```

- 模型**不内置**,首次使用按需下载到缓存目录(断点续传 + 校验)
- GPU 版 CTranslate2 需对应 CUDA 运行时;分发 CPU 版与 GPU 版两套,或运行期检测后引导下载 GPU 组件
- WebView2 用 evergreen bootstrapper 兜底;正式分发需代码签名以避开 SmartScreen

---

## 11. 实现约束清单

- 所有重计算异步执行,带进度回传和取消令牌;取消后清理临时文件(wav、中间 JSON)
- 错误归类:输入错误 / 引擎缺失 / 显存不足 / API 鉴权失败 / 网络错误 —— 各自给可读提示
- 显存不足时:各阶段串行执行;自动降模型档并提示
- ffmpeg/模型/字体分发需核对许可证(ffmpeg 用 LGPL 构建)
- 机翻定位为「校对前草稿」,产品必须提供字幕编辑器二次校对
```