"""Simple smoke test for the Test Program Intelligence tool."""
from __future__ import annotations

import asyncio
import os

from test_program_intelligence import Tools

QUESTION = "What is the current test program for PantherLake CPU-U"
EXPECTED_TOKEN = "PTUSDJXA1H21G402546"
PRODUCT_CODE = os.environ.get("TPFD_DEMO_PRODUCT", "8PXM")


def _pretty_header(title: str) -> str:
    return f"{title}\n{'=' * len(title)}\n"


async def _run_smoke_test() -> None:
    tool = Tools()
    answer = await tool.answer_tp_question(QUESTION, product_code=PRODUCT_CODE)
    print(_pretty_header("Tool Response"))
    print(answer)
    if EXPECTED_TOKEN not in answer:
        raise AssertionError(
            f"Expected '{EXPECTED_TOKEN}' in the response so we know the LatestTP is surfaced."
        )


if __name__ == "__main__":
    asyncio.run(_run_smoke_test())
