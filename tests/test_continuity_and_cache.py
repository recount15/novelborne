import unittest
from core.engine.export_continuity import report
from core.services.provider_cache import cache_key, build_prefix

class ContinuityAndCacheTests(unittest.TestCase):
    def test_report_detects_gap(self):
        r=report([{'round':1,'narrative':'a'},{'round':3,'narrative':'c'}])
        self.assertFalse(r['ok']); self.assertEqual(r['gaps'],[2])
    def test_cache_key_ignores_dynamic_layers(self):
        a=cache_key('openai','m',{'system_rules':'s','world_facts':'w','character_models':'c','output_contract':'o','action':'a'})
        b=cache_key('openai','m',{'system_rules':'s','world_facts':'w','character_models':'c','output_contract':'o','action':'b'})
        self.assertEqual(a,b)
    def test_prefix_stable(self): self.assertEqual(build_prefix({'system_rules':'s','world_facts':'w'}),'s\n\nw')

if __name__=='__main__': unittest.main()
