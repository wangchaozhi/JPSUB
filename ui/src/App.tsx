import { useEffect, useMemo, useRef, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { invoke } from '@tauri-apps/api/core';
import { open } from '@tauri-apps/plugin-dialog';
import './App.css';

type Stage =
  | 'pending'
  | 'extracting'
  | 'transcribing'
  | 'translating'
  | 'muxing'
  | 'done'
  | 'failed'
  | 'canceled';

type OutputFormat = 'srt' | 'ass';
type Engine = 'local' | 'online';

type Capabilities = {
  gpu: boolean;
  compute_type: string;
  default_model: string;
};

type GlossaryItem = {
  src: string;
  dst: string;
};

type TaskOptions = {
  model: string | null;
  device: string | null;
  engine: Engine;
  engine_params: {
    provider?: string;
    model_name?: string;
    base_url?: string;
    api_key?: string;
  };
  output_format: OutputFormat;
  bilingual: boolean;
  burn_in: boolean;
  glossary: GlossaryItem[];
};

type TaskError = {
  kind: string;
  message: string;
  hint?: string;
};

type TaskEvent = {
  stage: Stage;
  progress: number;
  error: TaskError | null;
};

type Segment = {
  index: number;
  start: number;
  end: number;
  text_src: string;
  text_zh: string;
};

type SaveSegmentsResponse = {
  segments: Segment[];
  result: Record<string, string>;
};

type PathFile = File & {
  path?: string;
};

const defaultOptions: TaskOptions = {
  model: null,
  device: null,
  engine: 'local',
  engine_params: {
    model_name: 'qwen2.5',
    provider: 'openai',
  },
  output_format: 'srt',
  bilingual: false,
  burn_in: false,
  glossary: [],
};

const stageLabels: Record<Stage, string> = {
  pending: '等待',
  extracting: '抽音轨',
  transcribing: '识别',
  translating: '翻译',
  muxing: '导出',
  done: '完成',
  failed: '失败',
  canceled: '已取消',
};

const terminalStages = new Set<Stage>(['done', 'failed', 'canceled']);

function normalizeBaseUrl(url: string) {
  return url.trim().replace(/\/+$/, '');
}

function formatSeconds(value: number) {
  if (!Number.isFinite(value)) return '00:00.000';
  const totalMs = Math.max(0, Math.round(value * 1000));
  const minutes = Math.floor(totalMs / 60000);
  const seconds = Math.floor((totalMs % 60000) / 1000);
  const millis = totalMs % 1000;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(
    millis,
  ).padStart(3, '0')}`;
}

function cleanOptions(options: TaskOptions): TaskOptions {
  return {
    ...options,
    glossary: options.glossary.filter((item) => item.src.trim() || item.dst.trim()),
  };
}

function onlineProvider(params: TaskOptions['engine_params']) {
  return params.provider || 'openai';
}

async function describeHttpError(response: Response) {
  let detail = '';
  try {
    const payload = (await response.clone().json()) as { detail?: unknown };
    if (typeof payload.detail === 'string') {
      detail = payload.detail;
    }
  } catch {
    try {
      detail = await response.text();
    } catch {
      detail = '';
    }
  }
  return `HTTP ${response.status}${detail ? `: ${detail}` : ''}`;
}

export function App() {
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  const [manualBaseUrl, setManualBaseUrl] = useState('');
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [inputPath, setInputPath] = useState('');
  const [options, setOptions] = useState<TaskOptions>(defaultOptions);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [taskEvent, setTaskEvent] = useState<TaskEvent>({
    stage: 'pending',
    progress: 0,
    error: null,
  });
  const [segments, setSegments] = useState<Segment[]>([]);
  const [result, setResult] = useState<Record<string, string>>({});
  const [message, setMessage] = useState('等待引擎就绪');
  const [isDragging, setIsDragging] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [credentialStatus, setCredentialStatus] = useState<Record<string, boolean>>({});
  const [isCredentialBusy, setIsCredentialBusy] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  const apiBaseUrl = useMemo(() => {
    const raw = manualBaseUrl || baseUrl || '';
    return normalizeBaseUrl(raw);
  }, [baseUrl, manualBaseUrl]);

  const canStart = Boolean(apiBaseUrl && inputPath.trim());
  const isRunning = !terminalStages.has(taskEvent.stage) && taskId !== null;
  const progressPercent = Math.round(taskEvent.progress * 100);
  const currentProvider = onlineProvider(options.engine_params);
  const hasStoredApiKey = Boolean(credentialStatus[currentProvider]);

  useEffect(() => {
    let disposed = false;
    const unlisten: Array<() => void> = [];

    void listen<{ base_url: string }>('engine-ready', (event) => {
      setBaseUrl(event.payload.base_url);
      setMessage('引擎已就绪');
    })
      .then((fn) => {
        if (disposed) {
          fn();
          return;
        }
        unlisten.push(fn);
      })
      .catch(() => {
        setMessage('可手动填写引擎地址');
      });

    void listen<number | null>('engine-exit', () => {
      setBaseUrl(null);
      setCapabilities(null);
      setMessage('引擎已退出，正在准备重启');
    })
      .then((fn) => {
        if (disposed) {
          fn();
          return;
        }
        unlisten.push(fn);
      })
      .catch(() => {
        setMessage('可手动填写引擎地址');
      });

    void listen<number>('engine-restarting', (event) => {
      setMessage(`引擎正在第 ${event.payload} 次重启`);
    })
      .then((fn) => {
        if (disposed) {
          fn();
          return;
        }
        unlisten.push(fn);
      })
      .catch(() => {
        setMessage('可手动填写引擎地址');
      });

    void listen<string>('engine-error', (event) => {
      setBaseUrl(null);
      setCapabilities(null);
      setMessage(`引擎启动失败: ${event.payload}`);
    })
      .then((fn) => {
        if (disposed) {
          fn();
          return;
        }
        unlisten.push(fn);
      })
      .catch(() => {
        setMessage('可手动填写引擎地址');
      });

    void invoke<string | null>('engine_base_url')
      .then((url) => {
        if (url) {
          setBaseUrl(url);
          setMessage('引擎已就绪');
        }
      })
      .catch(() => {
        setMessage('可手动填写引擎地址');
      });

    return () => {
      disposed = true;
      unlisten.forEach((fn) => fn());
    };
  }, []);

  useEffect(() => {
    if (!apiBaseUrl) return;

    const controller = new AbortController();
    void fetch(`${apiBaseUrl}/capabilities`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<Capabilities>;
      })
      .then((data) => {
        setCapabilities(data);
        setMessage(data.gpu ? '检测到 CUDA 加速' : '当前使用 CPU 推理');
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setCapabilities(null);
        setMessage('引擎地址暂不可用');
      });

    return () => controller.abort();
  }, [apiBaseUrl]);

  useEffect(() => {
    if (options.engine !== 'online') return;

    let disposed = false;
    void invoke<boolean>('api_key_status', { provider: currentProvider })
      .then((hasKey) => {
        if (disposed) return;
        setCredentialStatus((current) => ({ ...current, [currentProvider]: hasKey }));
      })
      .catch(() => {});

    return () => {
      disposed = true;
    };
  }, [currentProvider, options.engine]);

  function apiUrl(path: string) {
    if (!apiBaseUrl) {
      throw new Error('engine is not ready');
    }
    return `${apiBaseUrl}${path}`;
  }

  async function restartEngine() {
    setMessage('正在拉起引擎');
    try {
      await invoke('restart_engine');
    } catch (error) {
      setMessage(`引擎启动失败: ${String(error)}`);
    }
  }

  async function saveCurrentApiKey() {
    const provider = currentProvider;
    const apiKey = (options.engine_params.api_key || '').trim();
    if (!apiKey) {
      setMessage('请先输入 API key');
      return;
    }
    setIsCredentialBusy(true);
    try {
      await invoke('save_api_key', { provider, apiKey });
      setCredentialStatus((current) => ({ ...current, [provider]: true }));
      setMessage('API key 已保存到 Windows 凭据');
    } catch (error) {
      setMessage(`保存 API key 失败: ${String(error)}`);
    } finally {
      setIsCredentialBusy(false);
    }
  }

  async function loadCurrentApiKey() {
    const provider = currentProvider;
    setIsCredentialBusy(true);
    try {
      const apiKey = await invoke<string | null>('load_api_key', { provider });
      if (apiKey) {
        updateEngineParam('api_key', apiKey);
        setCredentialStatus((current) => ({ ...current, [provider]: true }));
        setMessage('已从 Windows 凭据读取 API key');
      } else {
        setCredentialStatus((current) => ({ ...current, [provider]: false }));
        setMessage('未找到已保存的 API key');
      }
    } catch (error) {
      setMessage(`读取 API key 失败: ${String(error)}`);
    } finally {
      setIsCredentialBusy(false);
    }
  }

  async function deleteCurrentApiKey() {
    const provider = currentProvider;
    setIsCredentialBusy(true);
    try {
      await invoke('delete_api_key', { provider });
      updateEngineParam('api_key', '');
      setCredentialStatus((current) => ({ ...current, [provider]: false }));
      setMessage('已删除保存的 API key');
    } catch (error) {
      setMessage(`删除 API key 失败: ${String(error)}`);
    } finally {
      setIsCredentialBusy(false);
    }
  }

  async function optionsForRun() {
    const next = cleanOptions(options);
    if (next.engine !== 'online' || next.engine_params.api_key) {
      return next;
    }

    const provider = onlineProvider(next.engine_params);
    try {
      const apiKey = await invoke<string | null>('load_api_key', { provider });
      if (apiKey) {
        setCredentialStatus((current) => ({ ...current, [provider]: true }));
        return {
          ...next,
          engine_params: {
            ...next.engine_params,
            api_key: apiKey,
          },
        };
      }
    } catch {
      // The engine will surface a missing key error if no runtime key is available.
    }
    return next;
  }

  function updateOption<K extends keyof TaskOptions>(key: K, value: TaskOptions[K]) {
    setOptions((current) => ({ ...current, [key]: value }));
  }

  function updateEngineParam(key: keyof TaskOptions['engine_params'], value: string) {
    setOptions((current) => ({
      ...current,
      engine_params: {
        ...current.engine_params,
        [key]: value,
      },
    }));
  }

  function updateGlossary(index: number, key: keyof GlossaryItem, value: string) {
    setOptions((current) => {
      const glossary = current.glossary.map((item, itemIndex) =>
        itemIndex === index ? { ...item, [key]: value } : item,
      );
      return { ...current, glossary };
    });
  }

  function addGlossary() {
    setOptions((current) => ({
      ...current,
      glossary: [...current.glossary, { src: '', dst: '' }],
    }));
  }

  function removeGlossary(index: number) {
    setOptions((current) => ({
      ...current,
      glossary: current.glossary.filter((_, itemIndex) => itemIndex !== index),
    }));
  }

  function pickDroppedFile(file: PathFile | undefined) {
    if (!file) return;
    if (file.path) {
      setInputPath(file.path);
      setMessage('已载入视频路径');
      return;
    }
    setInputPath(file.name);
    setMessage('浏览器预览模式只拿到文件名');
  }

  async function chooseVideoFile() {
    try {
      const selected = await open({
        multiple: false,
        filters: [
          {
            name: '视频文件',
            extensions: ['mp4', 'mkv', 'mov', 'avi', 'webm', 'm4v'],
          },
        ],
      });
      const path = Array.isArray(selected) ? selected[0] : selected;
      if (typeof path === 'string') {
        setInputPath(path);
        setMessage('已选择视频文件');
      }
    } catch {
      setMessage('当前预览环境不能打开系统文件选择器');
    }
  }

  function closeEventSource() {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
  }

  async function loadTaskOutput(id: string) {
    const [segmentsResponse, resultResponse] = await Promise.all([
      fetch(apiUrl(`/tasks/${id}/segments`)),
      fetch(apiUrl(`/tasks/${id}/result`)),
    ]);
    if (segmentsResponse.ok) {
      setSegments((await segmentsResponse.json()) as Segment[]);
    }
    if (resultResponse.ok) {
      setResult((await resultResponse.json()) as Record<string, string>);
    }
  }

  async function startTask() {
    if (!canStart) return;
    closeEventSource();
    setSegments([]);
    setResult({});
    setTaskEvent({ stage: 'pending', progress: 0, error: null });
    setMessage('任务已提交');

    const runOptions = await optionsForRun();
    let response: Response;
    try {
      response = await fetch(apiUrl('/tasks'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          input_path: inputPath.trim(),
          options: runOptions,
        }),
      });
    } catch {
      setMessage('创建任务失败: 无法连接引擎');
      return;
    }
    if (!response.ok) {
      setMessage(`创建任务失败: ${await describeHttpError(response)}`);
      return;
    }

    const data = (await response.json()) as { task_id: string };
    setTaskId(data.task_id);

    const source = new EventSource(apiUrl(`/tasks/${data.task_id}/events`));
    eventSourceRef.current = source;
    source.onmessage = (event) => {
      const next = JSON.parse(event.data) as TaskEvent;
      setTaskEvent(next);
      if (terminalStages.has(next.stage)) {
        closeEventSource();
        setMessage(stageLabels[next.stage]);
        void loadTaskOutput(data.task_id).catch(() => {
          setMessage('结果读取失败');
        });
      }
    };
    source.onerror = () => {
      closeEventSource();
      setMessage('进度连接中断');
    };
  }

  async function cancelTask() {
    if (!taskId) return;
    try {
      const response = await fetch(apiUrl(`/tasks/${taskId}/cancel`), { method: 'POST' });
      setMessage(response.ok ? '已发送取消' : `取消失败: ${await describeHttpError(response)}`);
    } catch {
      setMessage('取消失败: 无法连接引擎');
    }
  }

  function updateSegment(index: number, key: keyof Segment, value: string | number) {
    setSegments((current) =>
      current.map((segment, itemIndex) =>
        itemIndex === index
          ? {
              ...segment,
              [key]: value,
            }
          : segment,
      ),
    );
  }

  function splitSegment(index: number) {
    setSegments((current) => {
      const segment = current[index];
      if (!segment) return current;
      const midpoint = Number(((segment.start + segment.end) / 2).toFixed(3));
      const first = { ...segment, end: midpoint };
      const second = {
        ...segment,
        index: segment.index + 1,
        start: midpoint,
        text_zh: '',
      };
      return [...current.slice(0, index), first, second, ...current.slice(index + 1)].map(
        (item, itemIndex) => ({ ...item, index: itemIndex }),
      );
    });
  }

  function mergeWithPrevious(index: number) {
    if (index <= 0) return;
    setSegments((current) => {
      const previous = current[index - 1];
      const selected = current[index];
      if (!previous || !selected) return current;
      const merged = {
        ...previous,
        end: selected.end,
        text_src: `${previous.text_src}\n${selected.text_src}`.trim(),
        text_zh: `${previous.text_zh}\n${selected.text_zh}`.trim(),
      };
      return [...current.slice(0, index - 1), merged, ...current.slice(index + 1)].map(
        (item, itemIndex) => ({ ...item, index: itemIndex }),
      );
    });
  }

  async function saveSegments() {
    if (!taskId || segments.length === 0) return;
    setIsSaving(true);
    setMessage('正在回写字幕');
    try {
      const response = await fetch(apiUrl(`/tasks/${taskId}/segments`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(segments),
      });
      if (!response.ok) {
        setMessage(`保存失败: ${await describeHttpError(response)}`);
        return;
      }
      const data = (await response.json()) as SaveSegmentsResponse;
      setSegments(data.segments);
      setResult(data.result);
      setMessage('字幕已重新导出');
    } catch {
      setMessage('保存失败: 无法连接引擎');
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>日语视频转中文字幕</h1>
          <p>{message}</p>
        </div>
        <div className="engine-pill">
          <span className={apiBaseUrl ? 'dot ok' : 'dot'} />
          <span>{apiBaseUrl || '引擎未连接'}</span>
        </div>
      </header>

      <section className="workspace">
        <aside className="side-panel">
          <section
            className={`import-zone${isDragging ? ' dragging' : ''}`}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragging(false);
              pickDroppedFile(event.dataTransfer.files[0] as PathFile | undefined);
            }}
          >
            <div className="import-icon">+</div>
            <label htmlFor="input-path">视频路径</label>
            <div className="path-row">
              <input
                id="input-path"
                value={inputPath}
                onChange={(event) => setInputPath(event.target.value)}
                placeholder="E:\\video\\sample.mp4"
              />
              <button type="button" onClick={() => void chooseVideoFile()}>
                选择
              </button>
            </div>
          </section>

          <section className="panel-section">
            <div className="section-title">
              <h2>引擎</h2>
              <div className="button-row">
                <button
                  type="button"
                  onClick={() => void restartEngine()}
                  disabled={Boolean(apiBaseUrl)}
                >
                  重试
                </button>
                <button
                  type="button"
                  onClick={() => void startTask()}
                  disabled={!canStart || isRunning}
                >
                  开始
                </button>
              </div>
            </div>
            <label>
              本地接口
              <input
                value={manualBaseUrl}
                onChange={(event) => setManualBaseUrl(event.target.value)}
                placeholder={baseUrl || 'http://127.0.0.1:端口'}
              />
            </label>
            <div className="cap-grid">
              <span>算力</span>
              <strong>{capabilities?.gpu ? 'CUDA' : 'CPU'}</strong>
              <span>精度</span>
              <strong>{capabilities?.compute_type || '-'}</strong>
              <span>模型</span>
              <strong>{capabilities?.default_model || '-'}</strong>
              <span>预估</span>
              <strong>{capabilities?.gpu ? '0.5-1x' : '2-5x'}</strong>
            </div>
          </section>

          <section className="panel-section">
            <h2>设置</h2>
            <div className="control-grid">
              <label>
                Whisper
                <select
                  value={options.model ?? 'auto'}
                  onChange={(event) =>
                    updateOption('model', event.target.value === 'auto' ? null : event.target.value)
                  }
                >
                  <option value="auto">自适应</option>
                  <option value="medium">medium</option>
                  <option value="large-v3">large-v3</option>
                  <option value="small">small</option>
                  <option value="base">base</option>
                </select>
              </label>
              <label>
                设备
                <select
                  value={options.device ?? 'auto'}
                  onChange={(event) =>
                    updateOption(
                      'device',
                      event.target.value === 'auto' ? null : event.target.value,
                    )
                  }
                >
                  <option value="auto">自适应</option>
                  <option value="cuda">CUDA</option>
                  <option value="cpu">CPU</option>
                </select>
              </label>
              <label>
                翻译
                <select
                  value={options.engine}
                  onChange={(event) => updateOption('engine', event.target.value as Engine)}
                >
                  <option value="local">本地</option>
                  <option value="online">在线</option>
                </select>
              </label>
              {options.engine === 'online' ? (
                <>
                  <label>
                    Provider
                    <select
                      value={options.engine_params.provider || 'openai'}
                      onChange={(event) => updateEngineParam('provider', event.target.value)}
                    >
                      <option value="openai">OpenAI</option>
                      <option value="deepl">DeepL</option>
                    </select>
                  </label>
                  <label>
                    模型/端点
                    <input
                      value={
                        options.engine_params.provider === 'deepl'
                          ? options.engine_params.base_url || ''
                          : options.engine_params.model_name || ''
                      }
                      onChange={(event) =>
                        updateEngineParam(
                          options.engine_params.provider === 'deepl' ? 'base_url' : 'model_name',
                          event.target.value,
                        )
                      }
                      placeholder={
                        options.engine_params.provider === 'deepl'
                          ? 'DeepL endpoint 可留空'
                          : 'gpt-4o-mini'
                      }
                    />
                  </label>
                  <div className="wide-control credential-control">
                    <label>
                      API key
                      <input
                        type="password"
                        value={options.engine_params.api_key || ''}
                        onChange={(event) => updateEngineParam('api_key', event.target.value)}
                        placeholder="仅本次运行使用"
                      />
                    </label>
                    <div className="credential-row">
                      <span>{hasStoredApiKey ? 'Windows 凭据已保存' : '未保存'}</span>
                      <button
                        type="button"
                        onClick={() => void saveCurrentApiKey()}
                        disabled={isCredentialBusy}
                      >
                        保存
                      </button>
                      <button
                        type="button"
                        onClick={() => void loadCurrentApiKey()}
                        disabled={isCredentialBusy || !hasStoredApiKey}
                      >
                        加载
                      </button>
                      <button
                        type="button"
                        onClick={() => void deleteCurrentApiKey()}
                        disabled={isCredentialBusy || !hasStoredApiKey}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                  {options.engine_params.provider === 'openai' ? (
                    <label className="wide-control">
                      Base URL
                      <input
                        value={options.engine_params.base_url || ''}
                        onChange={(event) => updateEngineParam('base_url', event.target.value)}
                        placeholder="https://api.openai.com/v1"
                      />
                    </label>
                  ) : null}
                </>
              ) : (
                <label>
                  本地模型
                  <input
                    value={options.engine_params.model_name || ''}
                    onChange={(event) => updateEngineParam('model_name', event.target.value)}
                  />
                </label>
              )}
            </div>

            <div className="segmented">
              <button
                type="button"
                className={options.output_format === 'srt' ? 'active' : ''}
                onClick={() => updateOption('output_format', 'srt')}
              >
                SRT
              </button>
              <button
                type="button"
                className={options.output_format === 'ass' ? 'active' : ''}
                onClick={() => updateOption('output_format', 'ass')}
              >
                ASS
              </button>
            </div>

            <div className="toggles">
              <label>
                <input
                  type="checkbox"
                  checked={options.bilingual}
                  onChange={(event) => updateOption('bilingual', event.target.checked)}
                />
                双语
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={options.burn_in}
                  onChange={(event) => updateOption('burn_in', event.target.checked)}
                />
                烧录
              </label>
            </div>
          </section>

          <section className="panel-section">
            <div className="section-title">
              <h2>术语表</h2>
              <button type="button" onClick={addGlossary}>
                +
              </button>
            </div>
            <div className="glossary-list">
              {options.glossary.map((item, index) => (
                <div className="glossary-row" key={`${index}-${item.src}`}>
                  <input
                    value={item.src}
                    onChange={(event) => updateGlossary(index, 'src', event.target.value)}
                    placeholder="原文"
                  />
                  <input
                    value={item.dst}
                    onChange={(event) => updateGlossary(index, 'dst', event.target.value)}
                    placeholder="译名"
                  />
                  <button type="button" onClick={() => removeGlossary(index)} title="删除">
                    ×
                  </button>
                </div>
              ))}
            </div>
          </section>
        </aside>

        <section className="main-panel">
          <section className="progress-panel">
            <div className="progress-head">
              <div>
                <span className="eyebrow">当前阶段</span>
                <h2>{stageLabels[taskEvent.stage]}</h2>
              </div>
              <div className="task-actions">
                <span>{progressPercent}%</span>
                <button type="button" onClick={() => void cancelTask()} disabled={!isRunning}>
                  取消
                </button>
              </div>
            </div>
            <div className="progress-track">
              <div style={{ width: `${progressPercent}%` }} />
            </div>
            {taskEvent.error ? (
              <div className="error-box">
                <strong>{taskEvent.error.kind}</strong>
                <span>{taskEvent.error.message}</span>
                {taskEvent.error.hint ? <small>{taskEvent.error.hint}</small> : null}
              </div>
            ) : null}
          </section>

          <section className="result-strip">
            <span>任务</span>
            <strong>{taskId || '-'}</strong>
            <span>字幕</span>
            <strong>{result.srt_path || result.ass_path || '-'}</strong>
            <span>视频</span>
            <strong>{result.video_path || '-'}</strong>
          </section>

          <section className="editor-panel">
            <div className="section-title">
              <h2>字幕编辑器</h2>
              <button
                type="button"
                onClick={() => void saveSegments()}
                disabled={!taskId || segments.length === 0 || isSaving}
              >
                {isSaving ? '保存中' : '保存'}
              </button>
            </div>
            <div className="segment-list">
              {segments.length === 0 ? (
                <div className="empty-state">等待任务完成后载入分段</div>
              ) : (
                segments.map((segment, index) => (
                  <article className="segment-row" key={`${segment.index}-${segment.start}`}>
                    <div className="segment-meta">
                      <strong>{index + 1}</strong>
                      <span>{formatSeconds(segment.start)}</span>
                      <span>{formatSeconds(segment.end)}</span>
                      <button
                        type="button"
                        onClick={() => mergeWithPrevious(index)}
                        disabled={index === 0}
                      >
                        合并
                      </button>
                      <button type="button" onClick={() => splitSegment(index)}>
                        拆分
                      </button>
                    </div>
                    <div className="time-grid">
                      <label>
                        起点
                        <input
                          type="number"
                          min="0"
                          step="0.01"
                          value={segment.start}
                          onChange={(event) =>
                            updateSegment(index, 'start', Number(event.target.value))
                          }
                        />
                      </label>
                      <label>
                        终点
                        <input
                          type="number"
                          min="0"
                          step="0.01"
                          value={segment.end}
                          onChange={(event) =>
                            updateSegment(index, 'end', Number(event.target.value))
                          }
                        />
                      </label>
                    </div>
                    <label>
                      日文
                      <textarea
                        value={segment.text_src}
                        onChange={(event) => updateSegment(index, 'text_src', event.target.value)}
                      />
                    </label>
                    <label>
                      中文
                      <textarea
                        value={segment.text_zh}
                        onChange={(event) => updateSegment(index, 'text_zh', event.target.value)}
                      />
                    </label>
                  </article>
                ))
              )}
            </div>
          </section>
        </section>
      </section>
    </main>
  );
}
