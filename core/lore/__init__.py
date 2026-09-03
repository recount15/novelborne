"""动态世界书：按关键词、优先级和预算选择相关条目。"""
from .schema import LoreEntry, load_entries
from .matcher import match_entries
from .injector import LoreInjector

__all__ = ["LoreEntry", "load_entries", "match_entries", "LoreInjector"]
