import unittest
from unittest.mock import patch
from core.engine.export_continuity import report
from core.engine.turn_transaction import TurnTransaction
from core.services.story_agent import StoryAgent
from core.services import turn_pipeline

class StoryAgentIntegrationTests(unittest.TestCase):
 def test_ledger_gap_is_not_ok(self): self.assertFalse(report([{'round':1,'narrative':'a'},{'round':3,'narrative':'c'}])['ok'])
 def test_transaction_cannot_commit_before_validation(self):
  t=TurnTransaction({'round':1})
  with self.assertRaises(ValueError): t.advance('COMMITTED')
 def test_agent_mode_delegates_active_path(self):
  """Active selection reaches the pipeline explicitly and returns its candidate."""
  state={'round':1}
  result=turn_pipeline.TurnResult(
   narrative='正文', options=[{'key':key,'text':f'行动{key}'} for key in 'ABCDEF'],
   agent_meta={'status':'validated_candidate','effective_strategy':'agent_cluster'})
  with patch.object(turn_pipeline,'run_turn',return_value=result) as run:
   self.assertIs(StoryAgent(mode='agent').run_turn(state), result)
  run.assert_called_once()
  candidate=run.call_args.args[0]
  self.assertIsNot(candidate,state)
  self.assertEqual(candidate['generation_strategy'],'agent_cluster')
  self.assertEqual(result.agent_meta['status'],'validated_candidate')
  self.assertEqual(state,{'round':1})
 def test_explicit_agent_rejects_legacy_without_fallback(self):
  from core.services.generation_skills import GateError
  state={'round':1}
  with patch.object(turn_pipeline,'run_turn',return_value=turn_pipeline.LEGACY) as run:
   with self.assertRaises(GateError) as caught:
    StoryAgent(mode='agent').run_turn(state)
  self.assertEqual(caught.exception.code,'explicit_agent_legacy_rejected')
  run.assert_called_once()
  self.assertEqual(run.call_args.args[0]['generation_strategy'],'agent_cluster')
  self.assertEqual(state,{'round':1})
 def test_shadow_mode_does_not_mutate(self):
  state={'round':1}
  with self.assertRaises(RuntimeError): StoryAgent(mode='shadow').run_turn(state)
  self.assertEqual(state, {'round':1})
 def test_failure_rolls_back_state(self):
  state={'round':1,'marker':'before'}
  def fail(candidate,*args,**kwargs):
   candidate['marker']='changed'
   raise RuntimeError('upstream')
  with patch.object(turn_pipeline,'run_turn',side_effect=fail):
   with self.assertRaises(RuntimeError): StoryAgent(mode='agent').run_turn(state)
  self.assertEqual(state, {'round':1,'marker':'before'})
 def test_invalid_result_raises_and_rolls_back(self):
  """非 LEGACY 结果但选项形状非法：校验拒绝、事务回滚、状态不被污染。"""
  state={'round':2,'marker':'before'}
  bad=turn_pipeline.TurnResult(narrative='正文', options=[{'key':'A','text':'x'}])
  with patch.object(turn_pipeline,'run_turn',return_value=bad):
   with self.assertRaises(ValueError): StoryAgent(mode='agent').run_turn(state)
  self.assertEqual(state, {'round':2,'marker':'before'})
 def test_valid_result_passes_validation(self):
  """合法 TurnResult（A-F 完整）：通过校验并原样返回给调用方。"""
  state={'round':2}
  good=turn_pipeline.TurnResult(narrative='正文', options=[{'key':k,'text':'x'} for k in 'ABCDEF'])
  with patch.object(turn_pipeline,'run_turn',return_value=good):
   self.assertIs(StoryAgent(mode='agent').run_turn(state), good)

if __name__=='__main__': unittest.main()
