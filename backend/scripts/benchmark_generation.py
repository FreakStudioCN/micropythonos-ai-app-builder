"""Run one real generation and print a compact latency/result summary."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
load_dotenv(BACKEND_ROOT / ".env")

from app.generator import generate_app  # noqa: E402
from app.models import GenerateRequest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prompt",
        default="做一个极简四则运算计算器，按钮要大，适合触摸屏",
    )
    parser.add_argument("--package-name", default="com.example.speedtest")
    args = parser.parse_args()

    started = time.monotonic()
    try:
        result = asyncio.run(
            generate_app(
                GenerateRequest(
                    prompt=args.prompt,
                    package_name=args.package_name,
                    display_name="Generation Speed Test",
                )
            )
        )
    except Exception as exc:  # benchmark should always print elapsed time
        print(
            json.dumps(
                {
                    "ok": False,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "model": result.model,
                "filename": result.mpk_filename,
                "warnings": len(result.warnings),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
