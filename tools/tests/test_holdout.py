"""Independent synthetic FR/EN context contracts; no external inference."""
import unittest
from context_pack import prepare


class HoldoutTests(unittest.TestCase):
    def test_fr_en_evidence_and_instruction_retention(self):
        cases = [
            ('rollback deployment', 'Decision: rollback deployment requires health proof.'),
            ('migration Rust parser', 'Contrainte: migration Rust must preserve parser errors.'),
            ('confidentialité facture', 'Contrainte: confidentialité facture obligatoire.'),
            ('cache prefix stable', 'Decision: cache prefix stable; no forced warmup.'),
            ('Python cancellation', 'Constraint: Python cancellation must close pipes.'),
            ('JS serialization', 'Never change JS serialization without compatibility checks.'),
        ]
        for query, evidence in cases:
            with self.subTest(query=query):
                irrelevant = '\n'.join('Random unrelated notes about lunch.' for _ in range(400))
                pack = prepare(query, [('rules', 'Never send secrets.\nKeep API compatibility.')],
                               [('irrelevant', irrelevant), ('target', evidence)], 800)
                self.assertTrue(any(e['text'] == evidence for e in pack['excerpts']))
                self.assertEqual(pack['instructions'][0]['text'], 'Never send secrets.\nKeep API compatibility.')
                self.assertTrue(any(c['quote'] == evidence for c in pack['constraints_and_decisions']))
                self.assertLessEqual(pack['excerpt_characters'], 800)

    def test_long_single_line_is_retrievable(self):
        evidence = 'migration ' * 200
        pack = prepare('migration', [('rules', 'Keep sources')], [('minified', evidence)], 200)
        self.assertTrue(pack['excerpts'])
        self.assertEqual(pack['excerpts'][0]['text'], evidence[:200])
        self.assertGreater(pack['omitted_blocks'], 0)
