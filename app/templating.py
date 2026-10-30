"""共享的 Jinja2 模板环境。"""
from fastapi.templating import Jinja2Templates

from .config import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
