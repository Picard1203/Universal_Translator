# transformer_module/async_translator.py
"""
Async wrapper around your Transformer translation code
using **Beam‑Search (width = 4) only**.

• Finds the newest checkpoint inside transformer_module/**/tmodel_<src>-<tgt>_step_*.pt
• Loads & caches models + tokenizers
• Exposes async translate(text, src_lang, tgt_lang)
"""

import sys
import asyncio
from pathlib import Path
import re
from typing import List, Tuple

import torch
from tokenizers import Tokenizer

# ─── make bare imports like `from model import …` resolve here ─────────────
BASE_DIR = Path(__file__).parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
# ──────────────────────────────────────────────────────────────────────────

from config import get_config
from model   import build_transformer

# cache  (src, tgt) → (model, tok_src, tok_tgt, device, cfg)
_MODEL_CACHE: dict[Tuple[str, str], Tuple[torch.nn.Module,
                                          Tokenizer, Tokenizer,
                                          torch.device, dict]] = {}

# ╭────────────────────────────────────────────────────────────────────────╮
# │             checkpoint & model‑loading utilities                      │
# ╰────────────────────────────────────────────────────────────────────────╯
def _latest_checkpoint(cfg) -> Path | None:
    pattern = f"{cfg['model_basename']}step_*"
    files = list(BASE_DIR.rglob(pattern))
    if not files:
        return None
    files.sort(key=lambda p: int(re.search(r"step_(\d+)", p.stem).group(1)))
    return files[-1]

def _load_model_pair(src_lang: str, tgt_lang: str):
    key = (src_lang, tgt_lang)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    cfg = get_config().copy()
    cfg["lang_src"], cfg["lang_tgt"] = src_lang, tgt_lang
    direction = f"{src_lang}-{tgt_lang}"
    cfg["model_basename"]  = f"tmodel_{direction}_"
    cfg["experiment_name"] = f"runs/tmodel_{direction}"

    tok_src = Tokenizer.from_file(str(BASE_DIR / cfg["tokenizer_file"].format(src_lang)))
    tok_tgt = Tokenizer.from_file(str(BASE_DIR / cfg["tokenizer_file"].format(tgt_lang)))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_transformer(
        tok_src.get_vocab_size(),
        tok_tgt.get_vocab_size(),
        cfg["seq_len"], cfg["seq_len"],
        d_model=cfg["d_model"]
    ).to(device)

    ckpt = _latest_checkpoint(cfg)
    if ckpt is None:
        raise FileNotFoundError(f"No checkpoints for {direction}")
    state = torch.load(ckpt, map_location=device)
    clean = {k.replace("_orig_mod.", ""): v for k, v in state["model_state_dict"].items()}
    model.load_state_dict(clean)
    model.eval()

    _MODEL_CACHE[key] = (model, tok_src, tok_tgt, device, cfg)
    return _MODEL_CACHE[key]

# ╭────────────────────────────────────────────────────────────────────────╮
# │                         Beam‑Search decoder                           │
# ╰────────────────────────────────────────────────────────────────────────╯
def _beam_decode(model: torch.nn.Module,
                 src_tensor: torch.Tensor,
                 src_mask: torch.Tensor,
                 tok_src: Tokenizer,
                 tok_tgt: Tokenizer,
                 seq_len: int,
                 device: torch.device,
                 beam_width: int = 4,
                 len_penalty: float = 0.7) -> List[int]:
    sos = tok_tgt.token_to_id("[SOS]")
    eos = tok_tgt.token_to_id("[EOS]")
    pad = tok_tgt.token_to_id("[PAD]")

    beams = [(torch.tensor([sos], device=device, dtype=torch.int64), 0.0, False)]

    for _ in range(seq_len - 1):
        new_beams = []
        for seq, logp, finished in beams:
            if finished:
                new_beams.append((seq, logp, True))
                continue

            tgt_mask = (seq != pad).unsqueeze(0).unsqueeze(0).int()
            logits = model(src_tensor.unsqueeze(0), seq.unsqueeze(0),
                           src_mask, tgt_mask)[0, -1]      # (|V|)
            log_probs = logits.log_softmax(-1)

            top_logp, top_idx = torch.topk(log_probs, beam_width)
            for lp, tok_id in zip(top_logp, top_idx):
                new_seq = torch.cat([seq, tok_id.view(1)])
                new_beams.append((new_seq,
                                  logp + lp.item(),
                                  tok_id.item() == eos))

        # select best k with length penalty
        def score(item):
            seq, logp, _ = item
            length = seq.size(0)
            return logp / (length ** len_penalty)

        new_beams.sort(key=score, reverse=True)
        beams = new_beams[:beam_width]

        if all(b[2] for b in beams):   # all finished
            break

    best_seq = beams[0][0]
    return best_seq.tolist()

# ╭────────────────────────────────────────────────────────────────────────╮
# │                    synchronous translate (beam‑only)                  │
# ╰────────────────────────────────────────────────────────────────────────╯
def _sync_translate(txt: str,
                    src: str, tgt: str,
                    beam_width: int = 4) -> str:
    model, tok_src, tok_tgt, dev, cfg = _load_model_pair(src, tgt)

    seq_len = cfg["seq_len"]
    pad = tok_src.token_to_id("[PAD]")
    sos = tok_src.token_to_id("[SOS]")
    eos = tok_src.token_to_id("[EOS]")

    ids = tok_src.encode(txt).ids[: seq_len - 2]
    tokens = [sos] + ids + [eos] + [pad] * (seq_len - len(ids) - 2)
    src_tensor = torch.tensor(tokens, dtype=torch.int64).to(dev)
    src_mask = (src_tensor != pad).unsqueeze(0).unsqueeze(0).int().to(dev)

    with torch.no_grad():
        out_ids = _beam_decode(model, src_tensor, src_mask,
                               tok_src, tok_tgt,
                               seq_len, dev,
                               beam_width=beam_width)
    return tok_tgt.decode(out_ids)

# ──────────────────────────────────────────────────────────────────────────
#  Public async API  – beam_width is fixed at 4 unless caller overrides
# ──────────────────────────────────────────────────────────────────────────
async def translate(text: str,
                    src_lang: str,
                    tgt_lang: str,
                    beam_width: int = 4) -> str:
    """
    Asynchronous translation with Beam‑Search (default width = 4).
    Returns original text if anything goes wrong.
    """
    try:
        return await asyncio.to_thread(_sync_translate,
                                       text, src_lang, tgt_lang,
                                       beam_width)
    except Exception as exc:
        print(f"[async_translator] {src_lang}->{tgt_lang} ERROR: {exc}")
        return text
