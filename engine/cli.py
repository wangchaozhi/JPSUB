"""命令行入口:视频 -> 中文字幕。先用它跑通整条链路。

python -m engine.cli input.mp4 -o out.srt --model large-v3
"""

from __future__ import annotations

import argparse
import sys

from .models import Stage, Task, TaskOptions
from .orchestrator import run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="日语视频 -> 中文字幕")
    p.add_argument("input", help="输入视频路径")
    p.add_argument("-o", "--output", help="输出字幕路径(后缀决定格式)")
    p.add_argument("--model", help="whisper 模型档,默认自适应")
    p.add_argument("--device", choices=["cuda", "cpu"], help="默认自适应")
    p.add_argument("--engine", choices=["local", "online"], default="local")
    p.add_argument("--provider", help="在线引擎:deepl|google|openai")
    p.add_argument("--format", dest="fmt", choices=["srt", "ass"], default="srt")
    p.add_argument("--bilingual", action="store_true", help="双语字幕")
    p.add_argument("--burn-in", action="store_true", help="烧录硬字幕")
    args = p.parse_args(argv)

    options = TaskOptions(
        model=args.model,
        device=args.device,
        engine=args.engine,
        engine_params={"provider": args.provider} if args.provider else {},
        output_format=args.fmt,
        bilingual=args.bilingual,
        burn_in=args.burn_in,
    )
    task = Task(input_path=args.input, options=options)

    def on_update(t: Task):
        bar = "#" * int(t.progress * 30)
        sys.stderr.write(f"\r[{t.stage.value:<12}] {bar:<30} {t.progress*100:5.1f}%")
        sys.stderr.flush()

    run(task, on_update=on_update)
    sys.stderr.write("\n")

    if task.stage == Stage.DONE:
        print("完成:", task.result)
        return 0
    if task.stage == Stage.FAILED and task.error:
        print(f"失败[{task.error.kind}]: {task.error.message}  {task.error.hint}", file=sys.stderr)
    else:
        print(f"结束状态: {task.stage.value}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
