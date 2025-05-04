# src/translation.py

"""
Translation stub module.

Replaced with a thin async wrapper that calls into
transformer_module/async_translator.translate().
"""

import asyncio
from transformer_module.async_translator import translate as _real_translate

async def get_translation(
    text: str,
    source_lang: str,
    target_lang: str
) -> str:
    """
    Translate `text` from `source_lang` to `target_lang` using the
    real transformer models (cached, zero-hit on first call).

    Falls back to returning the original `text` if any error occurs.
    """
    try:
        result = await _real_translate(text, source_lang, target_lang)

        # If translator returned the same text (trim/case‑insensitive), treat as failure
        if result.strip().lower() == text.strip().lower():
            return ""
        return result
    except Exception:
        # In case of any unexpected error, return original text
        return text
