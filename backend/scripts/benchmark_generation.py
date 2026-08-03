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
    parser.add_argument("--provider", default="auto")
    args = parser.parse_args()

    started = time.monotonic()
    quality_attempts: list[dict[str, object]] = []
    try:
        result = asyncio.run(
            generate_app(
                GenerateRequest(
                    prompt=args.prompt,
                    package_name=args.package_name,
                    display_name="Generation Speed Test",
                    ai_provider=args.provider,
                ),
                attempt_sink=quality_attempts.append,
            )
        )
    except Exception as exc:  # benchmark should always print elapsed time
        safe_details = getattr(exc, "details", {})
        if not isinstance(safe_details, dict):
            safe_details = {}
        print(
            json.dumps(
                {
                    "ok": False,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "error_code": str(getattr(exc, "code", type(exc).__name__)),
                    "error": str(exc),
                    "attempted_providers": safe_details.get(
                        "attempted_providers", []
                    ),
                    "provider_attempts": safe_details.get("provider_attempts", []),
                    "quality_attempts": [
                        {
                            "attempt": item.get("attempt"),
                            "status": item.get("status"),
                            "provider": (
                                item.get("model_meta", {}).get("provider")
                                if isinstance(item.get("model_meta"), dict)
                                else None
                            ),
                            "validation": item.get("validation", {}),
                        }
                        for item in quality_attempts
                    ],
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
