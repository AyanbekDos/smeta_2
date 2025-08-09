#!/usr/bin/env python3
"""
Debug runner for the page-finding step with extra diagnostics.
Does NOT modify any existing code. Uses a separate prompt file:
  debug_find_and_validate_verbose.txt

Usage:
  python debug_run_find.py [PDF_PATH]
"""

import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath('.'))

from main_bot import (
    create_gemini_model,
    get_prompt,
    run_gemini_with_retry,
    parse_gemini_json,
    wait_for_gemini_file_active,
    USE_VERTEX_AI,
    GEMINI_TIMEOUT_SECONDS
)

from google.generativeai.types import GenerationConfig

import google.generativeai as genai


async def run(pdf_path: str):
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    user_id = 424242
    print(f"PDF: {pdf_path}")
    print(f"Size: {os.path.getsize(pdf_path)/1024/1024:.2f} MB")
    print(f"Gemini timeout: {GEMINI_TIMEOUT_SECONDS}s")

    prompt = get_prompt('debug_find_and_validate_verbose.txt')
    if not prompt:
        raise RuntimeError('Failed to load debug_find_and_validate_verbose.txt')

    model = create_gemini_model()
    print(f"Model: {getattr(model, 'model_name', 'unknown')}")

    if USE_VERTEX_AI:
        from vertexai.generative_models import Part as VPart
        with open(pdf_path, 'rb') as f:
            data = f.read()
        file_part = VPart.from_data(data, mime_type='application/pdf')
        response = await run_gemini_with_retry(
            model,
            prompt,
            file_part,
            user_id,
            generation_config=GenerationConfig(response_mime_type='application/json')
        )
    else:
        gemini_file = genai.upload_file(path=pdf_path)
        # ensure ACTIVE
        gemini_file = await wait_for_gemini_file_active(gemini_file, user_id)
        response = await run_gemini_with_retry(
            model,
            prompt,
            gemini_file,
            user_id,
            generation_config=GenerationConfig(response_mime_type='application/json')
        )
        try:
            genai.delete_file(gemini_file.name)
        except Exception:
            pass

    # Parse JSON (relaxed)
    result = parse_gemini_json(response, user_id, debug_tag='debug_find')
    print('\n==== Parsed JSON ===')
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # Print quick summary for convenience
    page = result.get('page', 0)
    print(f"\nSummary page: {page}")
    issues = result.get('issues') or []
    if issues:
        print('Issues:')
        for it in issues:
            print(' -', it)
    return result


if __name__ == '__main__':
    pdf = sys.argv[1] if len(sys.argv) > 1 else 'test/Ангар 24x40 КМ.pdf'
    print('Starting debug run...')
    res = asyncio.run(run(pdf))
    print('\nDone.')

