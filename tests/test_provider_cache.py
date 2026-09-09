"""Tests for provider-neutral cache key stability and prefix construction."""
import unittest
from core.services.provider_cache import cache_key, build_prefix, metadata


class ProviderCacheTests(unittest.TestCase):
    def test_cache_key_stable_for_same_layers(self):
        """Same stable layers produce identical cache keys."""
        layers_a = {
            'system_rules': 'rule1',
            'world_facts': 'fact1',
            'character_models': 'char1',
            'output_contract': 'contract1',
            'dynamic': 'different_a'
        }
        layers_b = {
            'system_rules': 'rule1',
            'world_facts': 'fact1',
            'character_models': 'char1',
            'output_contract': 'contract1',
            'dynamic': 'different_b'
        }
        key_a = cache_key('anthropic', 'claude-3-5-sonnet', layers_a)
        key_b = cache_key('anthropic', 'claude-3-5-sonnet', layers_b)
        self.assertEqual(key_a, key_b, 'Dynamic layers should not affect cache key')

    def test_cache_key_changes_with_stable_content(self):
        """Different stable content produces different keys."""
        layers_a = {'system_rules': 'rule1', 'world_facts': 'fact1'}
        layers_b = {'system_rules': 'rule1', 'world_facts': 'fact2'}
        key_a = cache_key('anthropic', 'claude-3-5-sonnet', layers_a)
        key_b = cache_key('anthropic', 'claude-3-5-sonnet', layers_b)
        self.assertNotEqual(key_a, key_b, 'Changed world_facts should invalidate cache')

    def test_cache_key_changes_with_provider(self):
        """Different providers produce different keys."""
        layers = {'system_rules': 'rule1', 'world_facts': 'fact1'}
        key_anthropic = cache_key('anthropic', 'claude-3-5-sonnet', layers)
        key_openai = cache_key('openai', 'gpt-4', layers)
        self.assertNotEqual(key_anthropic, key_openai, 'Different providers should have different keys')

    def test_cache_key_changes_with_model(self):
        """Different models produce different keys."""
        layers = {'system_rules': 'rule1', 'world_facts': 'fact1'}
        key_sonnet = cache_key('anthropic', 'claude-3-5-sonnet', layers)
        key_opus = cache_key('anthropic', 'claude-3-opus', layers)
        self.assertNotEqual(key_sonnet, key_opus, 'Different models should have different keys')

    def test_cache_key_changes_with_version(self):
        """Different versions produce different keys."""
        layers = {'system_rules': 'rule1', 'world_facts': 'fact1'}
        key_v1 = cache_key('anthropic', 'claude-3-5-sonnet', layers, version='1')
        key_v2 = cache_key('anthropic', 'claude-3-5-sonnet', layers, version='2')
        self.assertNotEqual(key_v1, key_v2, 'Different versions should have different keys')

    def test_build_prefix_includes_stable_layers_only(self):
        """Prefix only includes stable cacheable layers."""
        layers = {
            'system_rules': 'You are a story agent.',
            'world_facts': 'World: Fantasy realm.',
            'character_models': 'Character: Hero.',
            'output_contract': 'Output: JSON.',
            'recent_turns': 'Turn 1: something happened.',
            'current_action': 'Player chose option A.'
        }
        prefix = build_prefix(layers)
        self.assertIn('You are a story agent.', prefix)
        self.assertIn('World: Fantasy realm.', prefix)
        self.assertIn('Character: Hero.', prefix)
        self.assertIn('Output: JSON.', prefix)
        self.assertNotIn('Turn 1: something happened.', prefix)
        self.assertNotIn('Player chose option A.', prefix)

    def test_build_prefix_handles_empty_layers(self):
        """Prefix handles missing or empty layers gracefully."""
        layers = {'system_rules': 'rule', 'world_facts': '', 'character_models': None}
        prefix = build_prefix(layers)
        self.assertIn('rule', prefix)
        self.assertNotIn('None', prefix)
        # Empty values shouldn't produce extra newlines
        self.assertLessEqual(prefix.count('\n\n\n'), 0)

    def test_metadata_records_cache_info(self):
        """Metadata captures provider, model, key hash, and hit status."""
        meta = metadata('anthropic', 'claude-3-5-sonnet', 'abc123', hit=True)
        self.assertEqual(meta['provider'], 'anthropic')
        self.assertEqual(meta['model'], 'claude-3-5-sonnet')
        self.assertEqual(meta['cache_key_hash'], 'abc123')
        self.assertTrue(meta['cache_hit'])

    def test_metadata_defaults_to_miss(self):
        """Metadata defaults to cache miss if not specified."""
        meta = metadata('openai', 'gpt-4', 'def456')
        self.assertFalse(meta['cache_hit'])


if __name__ == '__main__':
    unittest.main()
