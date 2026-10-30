"""岗位搜索。"""
from .model import SearchJob, SearchResult
from .runner import run_search

__all__ = ["SearchJob", "SearchResult", "run_search"]
