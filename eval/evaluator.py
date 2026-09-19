# eval/evaluator.py
import json, os, shutil, time, subprocess

from eval.prepare import prepare_inputs

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX_ROOT = os.path.join(PROJECT_ROOT, "eval", "sandbox")
DATASET_PATH = os.path.join(PROJECT_ROOT, "eval", "eval_dataset.json")
def extract_text(file_path: str) -> str:
    """提取文档全部文本（含表格单元格），供 contains 检查用"""
    if file_path.endswith(".docx"):
        from docx import Document
        doc = Document(file_path)
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:              # ← 关键：遍历表格
            for row in table.rows:
                for cell in row.cells:
                    parts.append(cell.text)
        return "\n".join(parts)
    # xlsx / pptx 仍然走原来的 officecli view text
    result = subprocess.run(
        ["officecli", "view", file_path, "text"],
        capture_output=True, encoding="utf-8", errors="replace")
    return result.stdout

def check_result(check, workdir):
    path = os.path.join(workdir, check.get("file", "out.docx"))
    if check.get("file_exists") and not os.path.exists(path):
        return False
    # 额外文件也要存在（任务 12 用）
    for f in check.get("extra_files", []):
        if not os.path.exists(os.path.join(workdir, f)):
            return False
    text = extract_text(path)
    if any(s not in text for s in check.get("contains", [])):
        return False
    if any(s in text for s in check.get("not_contains", [])):   # ← 新增
        return False
    if len(text.strip()) < check.get("min_text_len", 0):
        return False
    return True

def run_eval(run_fn, dataset_path=DATASET_PATH, repeat=3, max_tasks=None,only_ids=None):
    """run_fn: 接收 task 字符串、返回最终 state/结果的函数——
       这样同一个脚本可以评测 Baseline 和 Exp 各组。"""
    dataset = json.load(open(dataset_path, encoding="utf-8"))
    if max_tasks:
        dataset = dataset[:max_tasks]
    rows = []
    original_cwd = os.getcwd()

    for item in dataset:
        if only_ids and item["id"] not in only_ids:
            continue
        scores, retries, durations = [], [], []
        for r in range(repeat):
            workdir = os.path.join(SANDBOX_ROOT, f"{item['id']}_r{r}_{int(time.time())}")
            os.makedirs(workdir, exist_ok=True)
            prepare_inputs(item, workdir)

            # 把沙箱路径注入任务，模型生成的命令直接用绝对路径
            task_with_dir = (f"所有文件操作都在目录 {workdir} 中进行，"
                             f"创建/修改文件时使用完整路径。\n任务：{item['task']}")

            t0 = time.time()
            try:
                final = run_fn(task_with_dir)
            except Exception as e:
                print(f"  [异常] {type(e).__name__}: {e}")
                final = {"retry_count": 0, "_error": str(e)}
            durations.append(time.time() - t0)
            # os.chdir 那两行可以删了——它们对子进程本来就无效

            scores.append(check_result(item["check"], workdir))
            retries.append(final.get("retry_count", 0))
            print(f"  任务{item['id']} 第{r + 1}次: {'✓' if scores[-1] else '✗'}")

        rows.append({"id": item["id"], "type": item["type"],
                     "success_rate": sum(scores) / repeat,
                     "avg_retries": sum(retries) / repeat,
                     "avg_duration": round(sum(durations) / repeat, 1)})
        json.dump(rows, open(os.path.join(PROJECT_ROOT, "eval", "results.json"), "w",
                             encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\n════════ 评测汇总 ════════")
        print(f"{'任务':<4} {'类型':<14} {'成功率':<8} {'平均重试':<8} {'平均耗时'}")
        for row in rows:
            print(f"{row['id']:<4} {row['type']:<14} {row['success_rate'] * 100:>5.1f}%  "
                  f"{row['avg_retries']:>8.1f} {row['avg_duration']:>7.1f}s")
        overall = sum(r['success_rate'] for r in rows) / len(rows)
        print(f"\n总成功率: {overall * 100:.1f}%")
    return rows