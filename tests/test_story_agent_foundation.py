import unittest
from core.engine.story_snapshot import build_snapshot
from core.engine.story_graph import check_events
from core.engine.story_weighting import factor_weight
from core.services.story_context import ContextLayer, fit_layers
from core.services.choice_agent import filter_candidates, select_diverse, public_options
from core.engine.story_invariants import validate_transition

class StoryAgentFoundationTests(unittest.TestCase):
    def test_snapshot_hash_ignores_history(self):
        a=build_snapshot({'round':1,'history':[1]}); b=build_snapshot({'round':1,'history':[2]})
        self.assertEqual(a.state_hash,b.state_hash)
    def test_event_prerequisite(self): self.assertFalse(check_events([{'event_id':'b','prerequisites':['a']}])['ok'])
    def test_hard_factor_floor(self): self.assertGreaterEqual(factor_weight({'base':.1,'hardness':'hard'},'patch'),.7)
    def test_context_budget_drops_optional(self):
        result=fit_layers([ContextLayer('required','x'*100,0,True),ContextLayer('optional','y'*10000,10)],provider_limit=100,output_budget=10,safety=10)
        self.assertIn('optional',result['dropped_blocks'])
    def test_choice_path(self):
        c=filter_candidates([{'action':'a'},{'action':'','patch_valid':True},{'action':'b','requires_future_knowledge':True}],{})
        self.assertEqual([x['action'] for x in c],['a'])
        self.assertEqual(public_options(select_diverse(c+[{'action':'c'}])),[{'key':'A','text':'a','preview':'','factor':'剧情'},{'key':'B','text':'c','preview':'','factor':'剧情'}])
    def test_transition(self): self.assertTrue(validate_transition({'round':1},{'round':2,'save_stage':'committed'})['ok'])

if __name__=='__main__': unittest.main()
