import unittest
from core.engine.turn_transaction import TurnTransaction
from core.engine.counterfactual import simulate
from core.engine.story_invariants import validate_narrative
class RemainingFoundationTests(unittest.TestCase):
 def test_transaction_lifecycle(self):
  t=TurnTransaction({'round':1}); t.advance('PLANNED'); t.apply({'x':1}); t.advance('GENERATED'); t.advance('VALIDATED')
  with self.assertRaises(ValueError): t.advance('COMMITTED')
  self.assertEqual(t.candidate['x'],1)
 def test_rollback(self):
  t=TurnTransaction({'round':1}); t.advance('PLANNED'); t.apply({'round':9}); t.rollback(); self.assertEqual(t.candidate['round'],1)
 def test_counterfactual_does_not_mutate(self):
  s={'round':2}; r=simulate(s,{'x':1}); self.assertNotIn('x',s); self.assertEqual(r['state']['round'],3)
 def test_narrative_evidence(self): self.assertFalse(validate_narrative('a',required_terms=['b'])['ok'])
if __name__=='__main__': unittest.main()
