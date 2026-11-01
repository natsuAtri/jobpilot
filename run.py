"""本地启动脚本。

    python run.py

默认监听 127.0.0.1:8000，只对本机开放。

依赖查找顺序：先看当前 Python 环境有没有装（标准 venv / 全局安装），
没有再退回到项目目录下的 .venvlib。顺序不能反——如果把 .venvlib
无条件插到 sys.path 最前面，它会遮蔽你自己 venv 里的同名包，
而 .venvlib 是按某个特定 Python 版本编译的，混用会直接报导入错误。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
_VENVLIB = BASE / ".venvlib"


def _import_uvicorn():
    global USING_VENVLIB
    try:
        import uvicorn  # noqa: F401
        return uvicorn
    except ModuleNotFoundError:
        pass

    if _VENVLIB.is_dir():
        # .venvlib 里的扩展模块是 cp312 编译的：别的 Python 版本混用会报
        # 「cannot import name 'ArgsKwargs'」这类和真实原因无关的错
        if sys.version_info[:2] != (3, 12):
            sys.exit(
                f"当前 Python 是 {sys.version_info.major}.{sys.version_info.minor}，"
                "项目自带的 .venvlib 是为 3.12 编译的，不能混用。\n"
                "请新建对应版本的环境并安装依赖：\n"
                "    py -3.12 -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt\n"
                "或者直接 pip install -r requirements.txt 装到当前环境。"
            )
        sys.path.append(str(_VENVLIB))
        try:
            import uvicorn  # noqa: F811
            USING_VENVLIB = True
            return uvicorn
        except ModuleNotFoundError:
            pass

    sys.exit(
        "缺少依赖。请先安装：\n"
        "    pip install -r requirements.txt\n"
        "国内网络建议加镜像：\n"
        "    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
    )


USING_VENVLIB = False
# 先看当前环境是不是已经装过 transformers——这决定了下面混用 .venvlib 有没有风险
try:
    import transformers  # noqa: F401

    PREEXISTING_ML = True
except Exception:  # noqa: BLE001
    PREEXISTING_ML = False

uvicorn = _import_uvicorn()

if __name__ == "__main__":
    port = int(os.environ.get("RA_PORT", "8000"))
    host = os.environ.get("RA_HOST", "127.0.0.1")
    reload = os.environ.get("RA_RELOAD", "1") == "1"
    if USING_VENVLIB and PREEXISTING_ML:
        print(
            "⚠ 当前环境已装有 transformers，又叠加了项目自带的 .venvlib，\n"
            "  两边的版本可能不一致，语义检索会报\n"
            "      'BertConfig' object has no attribute 'sub_configs'。\n"
            "  建议把依赖装进当前环境后重新启动：\n"
            "      pip install -r requirements.txt -r requirements-rag.txt\n"
            "  自检：python tools/check_env.py\n"
        )
    print(f"JobPilot → http://{host}:{port}")
    print("按 Ctrl+C 停止")
    uvicorn.run("app.main:app", host=host, port=port, reload=reload)
