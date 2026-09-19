import subprocess, os

def prepare_inputs(item, workdir):
    """任务需要的输入文件，用脚本预置到沙箱里。"""
    if "data.xlsx" in item.get("inputs", []):
        # 直接用 officecli 造一个标准输入文件，保证每次评测输入一致
        path = os.path.join(workdir, "data.xlsx")
        subprocess.run(["officecli", "create", path], check=True)
        for cell, val in [("/Sheet1/A1", "产品"), ("/Sheet1/B1", "销量"),
                          ("/Sheet1/A2", "苹果"), ("/Sheet1/B2", "120"),
                          ("/Sheet1/A3", "香蕉"), ("/Sheet1/B3", "85")]:
            subprocess.run(["officecli", "set", path, cell,
                            "--prop", f"value={val}"], check=True)