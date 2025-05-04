from pathlib import Path
import re

def get_config():
    # Base hyperparameters + language settings
    cfg = {
        "batch_size": 16,
        "num_epochs": 20,
        "lr": 10 ** -4,
        "seq_len": 350,
        "d_model": 512,
        "datasource": "Helsinki-NLP/opus-100",
        "lang_src": "en",           # <-- for Hebrew→English swap these two
        "lang_tgt": "he",           #     i.e. lang_src="he", lang_tgt="en"
        "model_folder": "weights",
        "model_basename": None,     # will be set dynamically below
        "experiment_name": None,    # will be set dynamically below
        "preload": "latest",
        "tokenizer_file": "tokenizer_{0}.json",
        "save_every": 5000,
        "eval_every": 5000,
    }

    # Build a tag like "en-he" or "he-en"
    direction = f"{cfg['lang_src']}-{cfg['lang_tgt']}"

    # Use that tag in your checkpoint filenames and TensorBoard logs
    cfg['model_basename']  = f"tmodel_{direction}_"
    cfg['experiment_name'] = f"runs/tmodel_{direction}"

    return cfg


def get_weights_file_path(config, epoch: str):
    """
    Returns the full path to save/load a checkpoint for the given epoch name.
    Example: model_basename="tmodel_en-he_", epoch="step_5000"
    → "./Helsinki-NLP/opus-100_weights/tmodel_en-he_step_5000.pt"
    """
    model_folder = f"{config['datasource']}_{config['model_folder']}"
    model_filename = f"{config['model_basename']}{epoch}.pt"
    return str(Path('.') / model_folder / model_filename)


def latest_weights_file_path(config):
    """
    Globs for all files matching "<model_basename>step_*" and returns the
    one with the highest step number.
    """
    model_folder = f"{config['datasource']}_{config['model_folder']}"
    pattern = f"{config['model_basename']}step_*"
    weights_files = list(Path(model_folder).glob(pattern))
    if not weights_files:
        return None

    # Sort by the integer after 'step_'
    weights_files.sort(key=lambda f: int(re.findall(r'step_(\d+)', str(f))[-1]))
    return str(weights_files[-1])
