from pathlib import Path
from config import get_config, latest_weights_file_path 
from model import build_transformer
from tokenizers import Tokenizer
from datasets import load_dataset
from dataset import BilingualDataset
import torch
import sys

# Import the greedy decode function from your training module to reuse its logic.
from train import greedy_decode

def translate(sentence: str):
    # Define the device, config, and load tokenizers
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("Using device:", device)
    config = get_config()
    
    # Load tokenizers from file (ensure these are the same as built in training)
    tokenizer_src = Tokenizer.from_file(str(Path(config['tokenizer_file'].format(config['lang_src']))))
    tokenizer_tgt = Tokenizer.from_file(str(Path(config['tokenizer_file'].format(config['lang_tgt']))))
    
    # Build the model and load pretrained weights
    model = build_transformer(
        tokenizer_src.get_vocab_size(),
        tokenizer_tgt.get_vocab_size(),
        config["seq_len"],
        config['seq_len'],
        d_model=config['d_model']
    ).to(device)
    
    model_filename = latest_weights_file_path(config)
    state = torch.load(model_filename)
    new_state_dict = {k.replace('_orig_mod.', ''): v for k, v in state['model_state_dict'].items()}
    model.load_state_dict(new_state_dict)
    
    # If the sentence is a number, use it as an index into the test set to fetch a source sentence and target label.
    label = ""
    if isinstance(sentence, int) or (isinstance(sentence, str) and sentence.isdigit()):
        idx = int(sentence)
        ds = load_dataset(config['datasource'], f"{config['lang_src']}-{config['lang_tgt']}", split='all')
        ds = BilingualDataset(ds, tokenizer_src, tokenizer_tgt,
                              config['lang_src'], config['lang_tgt'], config['seq_len'])
        sentence = ds[idx]['src_text']
        label = ds[idx]['tgt_text']
    
    seq_len = config['seq_len']
    
    # Preprocess the source sentence: encode, add [SOS] and [EOS], and pad to the fixed sequence length.
    source_ids = tokenizer_src.encode(sentence).ids
    source_tensor = torch.tensor(
        [tokenizer_src.token_to_id('[SOS]')] +
        source_ids +
        [tokenizer_src.token_to_id('[EOS]')] +
        [tokenizer_src.token_to_id('[PAD]')] * (seq_len - len(source_ids) - 2),
        dtype=torch.int64
    ).to(device)
    
    # Build the source mask (same as in training)
    source_mask = (source_tensor != tokenizer_src.token_to_id('[PAD]')).unsqueeze(0).unsqueeze(0).int().to(device)
    
    # Use the shared greedy decoding function
    model.eval()
    with torch.no_grad():
        decoded_ids = greedy_decode(model, source_tensor, source_mask, tokenizer_src, tokenizer_tgt, seq_len, device)
    
    # Convert the decoded token IDs to a string
    predicted_text = tokenizer_tgt.decode(decoded_ids.cpu().numpy())
    
    # Print details to match inference output
    if label:
        print(f"{'ID:':>12}{idx}")
    print(f"{'SOURCE:':>12}{sentence}")
    if label:
        print(f"{'TARGET:':>12}{label}")
    print(f"{'PREDICTED:':>12}{predicted_text}")
    
    return predicted_text

if __name__ == "__main__":
    input_sentence = sys.argv[1] if len(sys.argv) > 1 else "I am not a very good a student."
    translate(input_sentence)
