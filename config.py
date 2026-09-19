import os
import yaml

_CFG = None
# config.yaml 永远和 config.py 在同一目录（项目根目录），与从哪里运行无关
_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

def load_config(path=_DEFAULT_PATH):
    global _CFG
    with open(path, encoding="utf-8") as f:
        _CFG = yaml.safe_load(f)
    return _CFG

def cfg():
    global _CFG
    if _CFG is None:
        load_config()
    return _CFG