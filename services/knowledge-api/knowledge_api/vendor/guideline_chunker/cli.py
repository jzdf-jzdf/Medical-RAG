"""特意保持精简的指南切块器命令行接口。"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterable, Sequence
from pathlib import Path

from .config import load_pipeline_config
from .deepseek import DeepSeekSemanticClient
from .pipeline import STATE_NAME, _load_state, resume_outputs, run_inputs
from .review import knowledge_apily_review_decisions, export_review_queue
from .storage import load_processing_report
from .taxonomy import load_taxonomy


# CLI 退出码约定：
# 0 表示可以直接使用输出；1 表示运行失败；2 表示已产出结果但仍需人工审核。
# 将“需审核”与“失败”区分开，便于批处理脚本采取不同的后续动作。
EXIT_CODE = {
    "completed": 0,
    "dry_run_completed": 0,
    "completed_with_review": 2,
    "failed": 1,
}

# 只允许标准日志级别，避免把任意字符串传给 logging。
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def aggregate_exit_code(statuses: Iterable[str]) -> int:
    """把多本指南的运行状态汇总为一个进程退出码。

    只要有一本失败，整批退出码就是 1；没有失败但存在待审核结果时返回 2。
    """

    codes = [EXIT_CODE[status] for status in statuses]
    if 1 in codes:
        return 1
    if 2 in codes:
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    """声明命令行结构及各子命令支持的参数。"""

    parser = argparse.ArgumentParser(prog="guideline_chunker")
    commands = parser.add_subparsers(dest="command", required=True)

    # run：从 Markdown 开始执行完整切块流水线。
    run = commands.add_parser("run")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--pattern", default="*.md")
    run.add_argument("--recursive", action="store_true")
    run.add_argument("--workers", type=int)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--force", action="store_true")
    run.add_argument("--no-cache", action="store_true")
    run.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVELS,
        default="INFO",
        help="终端进度日志级别（默认：INFO）",
    )

    # resume 复用断点状态和有效缓存继续处理；validate 只读取已有报告。
    for name in ("resume", "validate"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        if name == "resume":
            command.add_argument(
                "--log-level",
                type=str.upper,
                choices=LOG_LEVELS,
                default="INFO",
                help="终端进度日志级别（默认：INFO）",
            )

    # 人工审核命令共享一个由用户维护的 JSONL 决策文件。
    for name in ("review-export", "review-apply"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--review-file", type=Path, required=True)
    return parser


def _state_for_output(output: Path) -> dict:
    """从单本输出目录或只含一本指南的批次根目录定位断点状态。"""

    root = Path(output)
    state_path = root / STATE_NAME
    if not state_path.is_file():
        # 允许用户传入批次输出根目录，但此时必须能唯一确定一本指南。
        states = sorted(root.glob(f"*/{STATE_NAME}"))
        if len(states) != 1:
            raise ValueError("resume requires one guide output directory or a root with one state file")
        state_path = states[0]
    return _load_state(state_path)


def _client_for_output(output: Path) -> DeepSeekSemanticClient:
    """根据断点中保存的配置路径重建续跑所需的语义客户端。"""

    state = _state_for_output(output)
    config = load_pipeline_config(Path(state["config_path"]))
    return DeepSeekSemanticClient(config, load_taxonomy(config.taxonomy.path))


def _read_jsonl(path: Path) -> list[dict]:
    """严格读取人工审核 JSONL，不允许空行或非对象记录。"""

    values = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"blank review JSONL line {number}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"review JSONL line {number} must be an object")
        values.append(value)
    return values


def _print_results(results) -> int:
    """在标准输出打印稳定的制表符结果，并返回整批退出码。"""

    for result in results.documents:
        print(f"{result.source_doc}\t{result.status.value}\t{result.output_dir}")
        if result.error:
            print(result.error)
    return aggregate_exit_code([result.status.value for result in results.documents])


def main(argv: Sequence[str] | None = None) -> int:
    """命令行总入口：完成参数分派，不承载具体切块业务逻辑。"""

    args = build_parser().parse_args(argv)

    # 根日志级别保持 WARNING，只单独放开本项目 logger。
    # 这样 INFO/DEBUG 进度不会意外开启 openai、httpx 等依赖的详细请求日志。
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("guideline_chunker").setLevel(
        getattr(logging, getattr(args, "log_level", "WARNING"))
    )

    if args.command == "run":
        config = load_pipeline_config(args.config)
        # 命令行开关只修改本次运行的内存配置，不回写 YAML 文件。
        if args.no_cache:
            config.cache.enabled = False
        if args.workers is not None:
            if args.workers < 1:
                raise ValueError("--workers must be positive")
            config.deepseek.concurrent_requests = args.workers

        # dry-run 只执行确定性预处理和批次规划，因此不读取 API key，
        # 也不创建会发起网络请求的 DeepSeek 客户端。
        client = None
        if not args.dry_run:
            client = DeepSeekSemanticClient(config, load_taxonomy(config.taxonomy.path))
        return _print_results(
            run_inputs(
                args.input,
                args.output,
                config,
                client,
                pattern=args.pattern,
                recursive=args.recursive,
                dry_run=args.dry_run,
                force=args.force,
            )
        )

    if args.command == "resume":
        # 续跑前会由 pipeline 再次校验输入、配置和标签体系哈希。
        return _print_results(resume_outputs(args.output, _client_for_output(args.output)))

    if args.command == "validate":
        # validate 可以指向单本指南目录，也可以指向包含多本指南的输出根目录。
        root = Path(args.output)
        reports = [root / "processing_report.json"] if (root / "processing_report.json").is_file() else sorted(root.glob("*/processing_report.json"))
        if not reports:
            raise FileNotFoundError("no processing_report.json found")
        statuses = []
        for report_path in reports:
            report = load_processing_report(report_path)
            print(f"{report.source_doc}\t{report.status.value}\t{report_path.parent}")
            statuses.append(report.status.value)
        return aggregate_exit_code(statuses)

    if args.command == "review-export":
        # 只导出尚未被 MANUALLY_RESOLVED 记录解决的审核项。
        queue = export_review_queue(args.output, args.review_file)
        print(f"exported {len(queue)} review records to {args.review_file}")
        return 0

    if args.command == "review-apply":
        # 回填后会重新组装知识块、执行文档校验并事务式发布四个输出文件。
        result = apply_review_decisions(args.output, _read_jsonl(args.review_file))
        if result.rejected_fields:
            print("rejected fields: " + ", ".join(result.rejected_fields))
            return 1
        print(f"applied {len(result.audit_records)} audit records")
        return 0

    # argparse 已要求 command 必填；走到这里代表代码与解析器定义不一致。
    raise AssertionError("unreachable")
