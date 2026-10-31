"""环境自检：确认语义检索依赖可用、且版本互相兼容。

    python tools/check_env.py

退出码：0 全部正常；1 有问题（会打印原因和修法）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

OK = "\033[32m✓\033[0m" if sys.stdout.isatty() else "[OK]"
BAD = "\033[31m✗\033[0m" if sys.stdout.isatty() else "[!!]"

problems: list[str] = []


def line(mark: str, text: str) -> None:
    print(f"{mark} {text}")


def check_core() -> None:
    print("\n== 核心依赖 ==")
    for name in ("fastapi", "uvicorn", "jinja2", "pydantic", "pypdfium2", "httpx"):
        try:
            mod = __import__(name)
            version = getattr(mod, "__version__", "?")
            line(OK, f"{name:22s} {version}")
        except Exception as exc:  # noqa: BLE001
            line(BAD, f"{name:22s} 缺失（{type(exc).__name__}）")
            problems.append(f"缺少 {name}：pip install -r requirements.txt")


def check_ml() -> None:
    print("\n== 语义检索依赖（可选）==")
    versions: dict[str, str] = {}
    for name in ("transformers", "sentence_transformers", "torch", "numpy"):
        try:
            mod = __import__(name)
            versions[name] = getattr(mod, "__version__", "?")
            line(OK, f"{name:22s} {versions[name]:12s} {Path(mod.__file__).parent}")
        except Exception as exc:  # noqa: BLE001
            line(BAD, f"{name:22s} 缺失（{type(exc).__name__}）")

    if len(versions) < 4:
        problems.append(
            "语义检索依赖不全：pip install -r requirements-rag.txt\n"
            "      （不装也能用，语义证据与项目对口度会自动降级）"
        )
        return

    tf = versions["transformers"].split(".")
    st = versions["sentence_transformers"].split(".")
    try:
        tf_major, st_major = int(tf[0]), int(st[0])
    except ValueError:
        return
    if st_major >= 6 and tf_major < 5:
        line(BAD, f"版本不兼容：sentence-transformers {versions['sentence_transformers']} "
                  f"要求 transformers >= 5.0，当前是 {versions['transformers']}")
        problems.append(
            "版本冲突：同一个环境里升级 transformers\n"
            "      pip install -U 'transformers>=5.0,<6'"
        )
        return

    line(OK, "版本组合兼容")


def check_models() -> None:
    print("\n== 本地模型 ==")
    try:
        from app.rag.embed import model_path

        line(OK if model_path().exists() else BAD,
             f"embedding  bge-small-zh-v1.5  {'已就位' if model_path().exists() else '未下载'}")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"embedding 检查失败：{exc}")

    try:
        from app.rag import rerank

        path = rerank.active_path()
        line(OK if path else BAD, f"rerank      {path.name if path else '未下载'}")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"rerank 检查失败：{exc}")


def check_works() -> None:
    print("\n== 实际调用 ==")
    try:
        from app.rag.embed import embed

        vec = embed(["这是一次环境自检"])
        line(OK, f"embedding 编码成功（{len(vec[0])} 维）")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"embedding 编码失败：{type(exc).__name__}: {exc}")
        problems.append("embedding 调用失败，见上方报错")
        return

    try:
        from app.rag import rerank

        if not rerank.available():
            line(BAD, "rerank 未下载，语义精排将跳过")
            return
        scores = rerank.rerank("掌握 MySQL", ["熟练使用 MySQL", "会画画"])
        line(OK, f"rerank 调用成功（{scores[0]:.3f} / {scores[1]:.3f}）")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"rerank 调用失败：{type(exc).__name__}: {exc}")
        problems.append("rerank 调用失败，见上方报错")


def main() -> int:
    print(f"Python {sys.version.split()[0]}  {sys.executable}")
    check_core()
    check_ml()
    check_models()
    check_works()

    print("\n== 结论 ==")
    if problems:
        for p in problems:
            print(f"  · {p}")
        return 1
    print("  全部正常，语义检索可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
