import yaml

_CFG = None

def load_config(path="config.yaml"):
    global _CFG
    with open(path, encoding="utf-8") as f:
        _CFG = yaml.safe_load(f)
    return _CFG

def cfg():
    global _CFG
    if _CFG is None:
        load_config()
    return _CFG