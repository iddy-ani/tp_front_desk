"""Validation suite for the eight canonical Testing-Questions prompts."""
from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from test_program_intelligence import Tools

QUESTION_FILE = Path(__file__).with_name("Testing-Questions.json")
PRODUCT_CODE = os.environ.get("TPFD_DEMO_PRODUCT", "8PXM")

EXPECTED_TOKENS: Dict[int, Sequence[str]] = {
    1: ("PTUSDJXA1H21G402546",),
    2: ("Flows (", "Modules (", "ARR_CORE"),
    3: ("SCN_CORE::ATSPEED_CORE0", "SubFlow PREHVQK"),
    4: ("ATSPEED", "SCN_CORE::ATSPEED"),
    5: ("VCC continuity flows", "TPI_VCC::CONT_ALL_DC_E_START"),
    6: ("📊 Snapshot", "Representative PAS excerpt"),
    7: ("HVQK/Waterfall flow content", "🌊 HVQK waterfall coverage"),
    8: ("HVQK coverage for ARR_ATOM", "Module ARR::"),
}


def _load_questions() -> List[Tuple[int, str]]:
    if not QUESTION_FILE.exists():
        raise FileNotFoundError(f"Missing questions file: {QUESTION_FILE}")
    with QUESTION_FILE.open("r", encoding="utf-8") as handle:
        raw_text = handle.read()
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        payload = ast.literal_eval(raw_text)
    questions: List[Tuple[int, str]] = []
    for key in sorted(payload, key=lambda value: int(value)):
        entry = payload[key]
        question_key = f"Q{key}"
        question = entry.get(question_key)
        if not question:
            raise ValueError(f"Entry {key} missing question field '{question_key}'")
        questions.append((int(key), question))
    return questions


async def _run_suite() -> None:
    tool = Tools()
    failures: List[str] = []
    for qid, question in _load_questions():
        expected_tokens = EXPECTED_TOKENS.get(qid)
        if not expected_tokens:
            raise KeyError(f"No expected tokens configured for question {qid}")
        answer = await tool.answer_tp_question(question, product_code=PRODUCT_CODE)
        missing = [token for token in expected_tokens if token not in answer]
        header = f"Q{qid}: {question}"
        print("\n" + header)
        print("-" * len(header))
        print(answer)
        if missing:
            failures.append(
                f"Q{qid} missing tokens: {', '.join(missing)}"
            )
        else:
            print("✅ All expected tokens present")
    if failures:
        joined = "\n".join(failures)
        raise SystemExit(f"\n❌ Validation failed:\n{joined}\n")
    print("\n🎉 All questions validated successfully")


if __name__ == "__main__":
    asyncio.run(_run_suite())
