#!/usr/bin/env python3
# test_metaai_consignes.py — Issue #277 : contrat de consignes d'échange du pont Meta AI.
#
# Couvert :
#   1. chaque inbox écrite par dispatch_write_inbox embarque le bloc `consignes`
#      (version + texte autoportant) — la session Meta AI doit pouvoir boucler
#      l'échange sans contexte externe ;
#   2. le prompt d'injection UI (metaai_ui_dispatch.PROMPT_TEMPLATE, injecté par
#      le pont metaaid) est cohérent avec ces consignes (mêmes chemins, même
#      format de réponse) ;
#   3. la config du pont MCP accepte bridge_root (format actuel) et dav_root
#      (repli legacy).

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import metaai_taskstore as mts
import metaai_mcp_server as mms
import metaai_ui_dispatch as uid

TOKENS_OBLIGATOIRES = ("inbox/", "outbox/", "task_id:", "reponse:", "confiance:",
                       "dossier partagé", "pas de JSON")


class ConsignesInboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = mts.TaskStore(self.root / "tasks.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_inbox_embarque_consignes_versionnees(self):
        tid = self.store.enqueue("expert_delegate", {"question": "Avis sur ce commit ?"})
        task = self.store.claim_next(task_id=tid)
        path = self.store.dispatch_write_inbox(task, self.root / "inbox")
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        consignes = payload.get("consignes")
        self.assertIsInstance(consignes, dict)
        self.assertEqual(consignes["version"], mts.CONSIGNES_VERSION)
        for token in TOKENS_OBLIGATOIRES:
            self.assertIn(token, consignes["texte"])

    def test_consignes_autoportantes_sans_contexte_externe(self):
        # Le texte doit suffire seul : chemins exacts, format de sortie, task_id.
        texte = mts.CONSIGNES_ECHANGE
        self.assertIn("inbox/<task_id>.json", texte)
        self.assertIn("outbox/<task_id>.txt", texte)
        self.assertIn("confiance", texte)
        self.assertIn("task_id SANS le modifier", texte)

    def test_consignes_ne_sont_pas_ecrasees_par_l_appelant(self):
        # setdefault : un payload appelant avec un bloc consignes obsolete
        # (même clé) ne doit pas rétrograder le contrat.
        tid = self.store.enqueue("expert_delegate", {
            "question": "q",
            "consignes": {"version": "0", "texte": "ancien protocole"}})
        task = self.store.claim_next(task_id=tid)
        path = self.store.dispatch_write_inbox(task, self.root / "inbox")
        consignes = json.loads(Path(path).read_text(encoding="utf-8"))["consignes"]
        self.assertEqual(consignes["version"], mts.CONSIGNES_VERSION)
        self.assertNotIn("ancien protocole", consignes["texte"])


class PromptTemplateTests(unittest.TestCase):
    def test_template_coherent_avec_consignes(self):
        prompt = uid.build_prompt("tache-x")
        self.assertIn("inbox/tache-x.json", prompt)
        self.assertIn("outbox/tache-x.txt", prompt)
        self.assertIn("task_id: tache-x", prompt)
        self.assertIn("confiance", prompt)
        self.assertIn("consignes", prompt)  # renvoie vers le bloc embarqué
        # Même format de sortie des deux côtés du contrat : 3 lignes clé/valeur.
        self.assertIn("trois lignes", prompt)
        self.assertIn("trois lignes", mts.CONSIGNES_ECHANGE)


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _config(self, **kwargs):
        cfg = {"taskstore": str(self.root / "tasks.sqlite")}
        cfg.update(kwargs)
        p = self.root / "metaai.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        return mms.load_config(str(p))

    def test_bridge_root_format_actuel(self):
        cfg = self._config(bridge_root=str(self.root / "pont"))
        self.assertEqual(Path(cfg["bridge_root"]), self.root / "pont")

    def test_dav_root_legacy_reconnu(self):
        cfg = self._config(dav_root=str(self.root / "vieux"))
        self.assertEqual(Path(cfg["bridge_root"]), self.root / "vieux")
        self.assertNotIn("dav_root", cfg)


class CircuitDemandeurTests(unittest.TestCase):
    """#277 : le reçu du delegate embarque le mode d'emploi du circuit."""

    def test_circuit_versionne_et_complet(self):
        self.assertIn("version 1", mts.CIRCUIT_DEMANDEUR)
        self.assertIn("ADMISSION", mts.CIRCUIT_DEMANDEUR)
        self.assertIn("wait_result", mts.CIRCUIT_DEMANDEUR)
        self.assertIn("result.confiance", mts.CIRCUIT_DEMANDEUR)
        self.assertIn("nouvelle clé", mts.CIRCUIT_DEMANDEUR)

    def test_consignes_et_circuit_sont_distingues(self):
        # Consignes = pour la session Meta AI ; circuit = pour le harness
        # demandeur. Aucun des deux ne doit se substituer à l'autre.
        self.assertNotIn("wait_result", mts.CONSIGNES_ECHANGE)
        self.assertNotIn("dossier partagé", mts.CIRCUIT_DEMANDEUR)


if __name__ == "__main__":
    unittest.main()
