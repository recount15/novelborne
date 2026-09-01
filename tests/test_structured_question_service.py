import unittest
from core.services.structured_question_service import StructuredQuestionService, make_question, normalize_answer, validate_answer

class StructuredQuestionTests(unittest.TestCase):
    def setUp(self): self.q = make_question('gf_correction','scope','选择作用域',answer_type='single_choice',choices=[{'id':'self','label':'自身'},{'id':'world','label':'世界'}],evidence_refs=[{'id':'e1'}])
    def test_normalize_and_validate(self):
        self.assertEqual(normalize_answer(self.q,'自身'),'self'); self.assertEqual(validate_answer(self.q,'self'),[])
        self.assertTrue(validate_answer(self.q,'bad'))
    def test_batch_cache_and_fallback(self):
        s=StructuredQuestionService(); calls=[]
        first=s.batch([self.q],model=lambda q: calls.append(q['id']) or '自身'); second=s.batch([self.q],model=lambda q: calls.append('duplicate') or '世界')
        self.assertEqual(first[0]['answer'],'self'); self.assertTrue(second[0]['cached']); self.assertEqual(calls,['gf_correction.scope'])
    def test_fallback_marks_insufficient(self):
        r=StructuredQuestionService().fallback(self.q,error='offline'); self.assertTrue(r['insufficient_evidence']); self.assertEqual(r['source'],'fallback')

if __name__ == '__main__': unittest.main()
