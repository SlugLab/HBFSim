"""Host-only registration order; no tensor movement or device algorithm."""
from pathlib import Path
import hashlib,json
GLOBAL=('model.embed_tokens.weight','model.norm.weight','lm_head.weight')
ROLES=('mlp.experts.w13_weight','mlp.experts.w2_weight','input_layernorm.weight',
       'post_attention_layernorm.weight','self_attn.q_norm.weight','self_attn.k_norm.weight',
       'self_attn.qkv_proj.weight','self_attn.o_proj.weight','mlp.gate.weight')
LEDGER_SHA='666fb5bb13bb4206f30bd74986708ec0dc6a34d01ebd22d0e470c76dc10b4fda'
def registration_order(selected):
    path=Path(__file__).resolve().parents[1]/'pinned_scope/pinned/TARGET_LEDGER.json'
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=LEDGER_SHA: raise ValueError('registration ledger changed')
    entries={e['aliases'][0]:e for e in json.loads(raw)['entries']}
    canonical=list(GLOBAL)+[f'model.layers.{n}.{r}' for n in range(16) for r in ROLES]
    by_alias={}
    for storage in selected:
        names=storage.aliases
        if len(names)!=1 or names[0] not in entries or names[0] in by_alias:
            raise ValueError('ambiguous/noncanonical direct HBF storage')
        if storage.size!=entries[names[0]]['storage_bytes']:
            raise ValueError('direct HBF complete storage length differs')
        by_alias[names[0]]=storage
    count=len(by_alias)
    if count not in (39,75,111,147) or set(by_alias)!=set(canonical[:count]):
        raise ValueError('direct HBF scope is not the reviewed progressive prefix')
    return [by_alias[alias] for alias in canonical[:count]]
