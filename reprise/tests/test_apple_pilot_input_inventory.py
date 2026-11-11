"""Presentation-only evidence boundaries: no model or service is invoked."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.uname().sysname == 'Darwin', 'requires macOS Swift')
class InputInventoryTests(unittest.TestCase):
    def test_recorded_inputs_and_unknowns_without_semantic_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = (ROOT/'swift/ApplePilotUI.swift').read_text()
            helper = source.split('enum PreparationInputInventory {', 1)[1].split('private struct PreparationLink', 1)[0]
            (root/'Inventory.swift').write_text('import Foundation\nenum PreparationInputInventory {' + helper)
            (root/'main.swift').write_text('''import Foundation
let data = FileHandle.standardInput.readDataToEndOfFile()
let rows = try! JSONSerialization.jsonObject(with: data) as! [[String: Any]]
let result = rows.map { ["description": PreparationInputInventory.describe($0), "original": PreparationInputInventory.json($0)] }
print(PreparationInputInventory.json(result))
''')
            subprocess.run(['swiftc', '-module-cache-path', str(root/'cache'), str(root/'Inventory.swift'), str(root/'main.swift'), '-o', str(root/'probe')], check=True)
            request = 'Résumé du rapport Alpha. Document : premier fait non confirmé.'
            proposal = {'objective':'Analyse locale de préparation (reprise-analysis-v3).', 'constraints':[json.dumps({'request':request,'template':'reprise-analysis-v3'})], 'questions':[]}
            fact = {'id':'fact', 'revision':1, 'text':'Alpha vise des débutants', 'status':'asserted', 'source':{'reference':'synthetic'}, 'recorded_by':{}}
            selection = {'work_id':'w', 'snapshot_sha256':'abc', 'confirmed_at':1, 'snapshot':{'schema':'reprise-memory-selection-v1','project_id':'p','work_id':'w','facts':[fact]}}
            base = {'project_id':'p','proposal':proposal,'memory_selection':selection,'clarifications':[{'question':'Pourquoi ?', 'answer':None, 'disposition':'dismissed', 'reason':'Non bloquant'}]}
            rows = [base, dict(base, local_execution={'manifest':{'prompt_format':'preparation-analysis-v4-recall','memory_snapshot_sha256':'abc'}, 'receipt':{'response':'nested', 'run_id':'r'}}),
                    {'proposal':{'objective':'invented'},'candidate_import':{'origin':{'source_operation_id':'source'}}},
                    dict(base, memory_selection={'snapshot':{}}), dict(base, local_execution={'manifest':{'prompt_format':'future'}}),
                    dict(base, memory_selection={'snapshot':[], 'work_id':'w'})]
            result = json.loads(subprocess.run([str(root/'probe')], input=json.dumps(rows), text=True, capture_output=True, check=True).stdout)
            for row, output in zip(rows, result):
                self.assertEqual(json.loads(output['original']), row)
            first, frozen, imported, malformed, future, invalid = [item['description'] for item in result]
            self.assertIn(request, first)
            self.assertIn('Entrées préparées', first)
            self.assertIn('Aucune pièce documentaire distincte', first)
            self.assertIn('ne le détermine pas', first)
            self.assertIn('1 assertion(s) historiques', first)
            self.assertIn('synthetic', first)
            self.assertIn('Non bloquant', first)
            self.assertIn('null', first)
            self.assertIn('Entrées figées', frozen)
            self.assertIn('référencée par le manifeste', frozen)
            self.assertIn('entrées originales non chargées', imported)
            self.assertNotIn('Demande opérateur enregistrée', imported)
            self.assertIn('inventaire indisponible', malformed)
            self.assertIn('Format inconnu', future)
            self.assertIn('inventaire indisponible', invalid)
            self.assertEqual(json.loads(result[1]['original'])['local_execution']['receipt']['response'], 'nested')

    def test_views_expose_evidence_and_complete_receipt_without_actions(self):
        view = (ROOT/'swift/ApplePilotView.swift').read_text()
        self.assertIn('Reçu brut — vue JSON complète', view)
        self.assertIn('PreparationInputInventory.json(receipt)', view)
        self.assertGreaterEqual(view.count('inputInventoryPanel'), 3)
