# -*- coding: utf-8 -*-
"""Token 使用量计量测试（阶段 E）。"""
import pytest
from core.engine import token_accounting


class TestTurnUsageInit:
    """回合级累加器初始化测试。"""
    
    def test_init_creates_usage_dict(self):
        usage = token_accounting.init_turn_usage()
        assert usage["prompt_tokens"] == 0
        assert usage["completion_tokens"] == 0
        assert usage["total_tokens"] == 0
        assert "director" in usage
        assert "segments" in usage
    
    def test_get_turn_usage_returns_none_initially(self):
        # 重置 contextvar
        import contextvars
        token_accounting._turn_usage = contextvars.ContextVar("_turn_usage", default=None)
        usage = token_accounting.get_turn_usage()
        assert usage is None


class TestRecordUsage:
    """usage 记录测试。"""
    
    def test_record_usage_accumulates(self):
        usage = token_accounting.init_turn_usage()
        token_accounting.record_usage(100, 50, "director")
        token_accounting.record_usage(200, 100, "segments")
        
        assert usage["prompt_tokens"] == 300
        assert usage["completion_tokens"] == 150
        assert usage["total_tokens"] == 450
        assert usage["director"] == 150
        assert usage["segments"] == 300
    
    def test_record_usage_unknown_category(self):
        usage = token_accounting.init_turn_usage()
        token_accounting.record_usage(100, 50, "unknown_category")
        
        assert usage["total_tokens"] == 150
        assert usage["other"] == 150


class TestExtractUsage:
    """从 response 提取 usage 测试。"""
    
    def test_extract_usage_from_response(self):
        # 模拟 OpenAI response 对象
        class MockUsage:
            prompt_tokens = 100
            completion_tokens = 50
            total_tokens = 150
        
        class MockResponse:
            usage = MockUsage()
        
        response = MockResponse()
        usage = token_accounting.extract_usage(response)
        
        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 50
        assert usage["total_tokens"] == 150
    
    def test_extract_usage_no_usage_attr(self):
        class MockResponse:
            pass
        
        response = MockResponse()
        usage = token_accounting.extract_usage(response)
        assert usage is None


class TestEstimateTokens:
    """Token 估算测试。"""
    
    def test_estimate_chinese(self):
        text = "这是一段中文文本" * 10  # 80 个中文字
        tokens = token_accounting.estimate_tokens(text)
        # 中文 ~1.5 字/token，80 字 ≈ 53 tokens
        assert 40 < tokens < 70
    
    def test_estimate_english(self):
        text = "This is an English text " * 10  # ~240 字符
        tokens = token_accounting.estimate_tokens(text)
        # 英文 ~4 字符/token，240 字符 ≈ 60 tokens
        assert 40 < tokens < 100
    
    def test_estimate_mixed(self):
        text = "混合文本 mixed text 混合"
        tokens = token_accounting.estimate_tokens(text)
        assert tokens > 0
    
    def test_estimate_empty(self):
        tokens = token_accounting.estimate_tokens("")
        assert tokens == 0
