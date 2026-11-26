"""Tests pour competence_inputs (lot L5t, contrat E4-O1 @4d9228d).

TDD : le module reprise/competence_inputs.py (lot L5c) n'existe pas
encore ; la collecte echoue avec ImportError jusqu'a sa livraison —
rouge conforme.
Auteur L5t : ASI1 (SOD : l'auteur des tests n'est pas l'auteur du code L5c).

Spec : spec-e3-o1-competence 1.7, comportements T2 (quota_zero),
T6 (facturation_inconnue), T7 (ecriture_fermee), T9 (determinisme),
T10 (incertains), T12 (entree_corrompue), sur SQLite temporaire
(routing_events.setup + seed_local, record, finalize, declare,
record_refusal). Erratum revue stricte @e7e2458 : la sortie finale
porte les motifs en forme O1 {"code" in MOTIF_CODES, "valeur"
non-chaine, "source": {"type", "ref"}} et le refus global {"raison"
in REFUS_RAISONS, "route_rejetees"} — l'assemblage L5 mappe les
motifs internes L3, jamais d'adaptation silencieuse ; une
observation corrompue (issue inconnue) n'entre ni dans sample_count
ni dans les compteurs. Q9 (drapeau fait foi) : la table tool+level
est fournie avec le lot L5 ; tant que la jointure delegation_verdicts
(verdicts E6, table L0 pas encore livree) n'existe pas, les stats
restent non_mesure=true.

Contrat du module (aucune E/S reseau, SQLite fournie par l'appelant) :
- competence_inputs.observations(db, table) -> (obs, ignorees) :
  projette routing_events (rows de recent) en observations
  {"tool", "level", "backend", "issue", "ts"} ; issue derive de
  terminal_state : 'finished' -> 'succes_verifie', 'blocked' ->
  'echec', 'interrupted' -> 'incertain', toute autre valeur (dont
  NULL) -> observation corrompue, ignoree ; role/kind derives via
  competence_stats.derive_role_kind avec la table (cle (tool, level),
  valeur [role, kind]) ; une observation non projetable (absente de
  la table) est ignoree. Table None => tout ignore.
- competence_inputs.candidats(db, demande, table=None) -> dict
  indexe par actor : un candidat par backend du registre A0
  (backend_registry.BACKENDS) avec drapeaux precalcules pour
  competence_rank.classer : 'actor' ('acteur-<backend>'), 'backend',
  'billing_class' (traduction du billing routing via
  backend_registry.billing : 'unknown' reste 'unknown' => exclusion
  FACTURATION_INCONNUE), 'quota' (False si vetos(db, channel)
  contient 'quota-perime' ou 'refus-recent'), 'ecriture' (False si
  demande['mode'] == 'implementation' et workspace_write registre
  faux), 'consentie' False au jalon 1, 'sensible_ok' False (acteur
  distant), 'role_autorise', 'stats' None (jointure
  delegation_verdicts absente au jalon 1 => non_mesure fait foi),
  'prior' None. db None => {} (=> REGISTRE_ABSENT au rang).
- competence_inputs.classer_via_db(db, demande, table=None,
  maintenant=None) -> dict : assemble la sortie O1 FINALE validee par
  competence_types.valide_sortie (cles exactes {spec, base_sha,
  demande, decision, tracabilite, refus} ; decision.choisi =
  {"actor", "backend", "billing_class"}) ; demande corrompue (champ
  absent, None, mauvais type) => refus ENTREE_INVALIDE, jamais
  d'exception non rattrapee (T12) ; deux appels sur le meme etat =>
  sortie identique octet pour octet (T9) ; les incertains sont
  comptes a part dans tracabilite (T10).
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import competence_inputs  # noqa: E402
import competence_types  # noqa: E402
import routing_events  # noqa: E402

DEMANDE = {'role': 'revue', 'mode': 'expertise', 'kind': 'expertise',
           'sensible': False}
TABLE = {('reprise_delegate', 'standard'): ['revue', 'expertise'],
         ('reprise_delegate', 'deep'): ['revue', 'expertise']}


def _db():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    routing_events.setup(db)
    routing_events.seed_local(db)
    return db


def _seed(db, backend, tool='reprise_delegate', level='standard',
          terminal='dispatched', n=1):
    for _ in range(n):
        eid = routing_events.record(db, 'routed', tool, backend,
                                    level=level)
        if terminal != 'dispatched':
            routing_events.finalize(db, eid, terminal)


class T02QuotaZeroTest(unittest.TestCase):
    """T2 : meilleur backend a quota epuise (veto) => exclu, motif
    QUOTA ; seul candidat => choisi=null, refus=QUOTA_EPUISE."""

    def test_t02_quota_zero_seul_candidat(self):
        db = _db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.record_refusal(db, 'codex', 'quota epuise')
        _seed(db, 'codex')
        sortie = competence_inputs.classer_via_db(db, DEMANDE, table=TABLE)
        self.assertIsNone(sortie['decision']['choisi'])
        self.assertEqual(sortie['refus']['raison'], 'QUOTA_EPUISE')
        self.assertEqual(competence_types.valide_sortie(sortie), (True, []))
        db.close()

    def test_t02_quota_zero_concurrent_actif(self):
        db = _db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.record_refusal(db, 'codex', 'quota epuise')
        _seed(db, 'codex')
        _seed(db, 'asi1')
        sortie = competence_inputs.classer_via_db(db, DEMANDE, table=TABLE)
        self.assertEqual(sortie['decision']['choisi']['actor'],
                         'acteur-asi1')
        db.close()


class T06FacturationTest(unittest.TestCase):
    """T6 : billing routing unknown => exclu FACTURATION_INCONNUE."""

    def test_t06_facturation_inconnue_exclu(self):
        db = _db()
        routing_events.declare(db, 'claude-desktop', 'unknown')
        _seed(db, 'claude-desktop')
        sortie = competence_inputs.classer_via_db(db, DEMANDE, table=TABLE)
        self.assertIsNone(sortie['decision']['choisi'])
        self.assertEqual(sortie['refus']['raison'], 'FACTURATION_INCONNUE')
        self.assertEqual(competence_types.valide_sortie(sortie), (True, []))
        db.close()


class T07EcritureTest(unittest.TestCase):
    """T7 : mode=implementation, workspace_write registre faux =>
    exclu ECRITURE_NON_AUTORISEE."""

    def test_t07_ecriture_fermee_exclu(self):
        db = _db()
        _seed(db, 'asi1')
        demande = dict(DEMANDE, mode='implementation',
                       kind='implementation')
        sortie = competence_inputs.classer_via_db(db, demande, table=TABLE)
        self.assertIsNone(sortie['decision']['choisi'])
        self.assertEqual(sortie['refus']['raison'],
                         'ECRITURE_NON_AUTORISEE')
        db.close()


class T09DeterminismeTest(unittest.TestCase):
    """T9 : meme entree x2 => sortie identique octet pour octet."""

    def test_t09_determinisme_deux_appels(self):
        db = _db()
        _seed(db, 'asi1')
        _seed(db, 'asi1', level='deep')
        premier = competence_inputs.classer_via_db(db, DEMANDE, table=TABLE)
        second = competence_inputs.classer_via_db(db, DEMANDE, table=TABLE)
        self.assertEqual(premier, second)
        self.assertEqual(competence_types.valide_sortie(premier), (True, []))
        db.close()


class T10IncertainsTest(unittest.TestCase):
    """T10 : les incertains comptent a part, ni succes ni echec,
    reportes dans tracabilite ; observation corrompue ignoree ;
    non_mesure=true fait foi au jalon 1 (Q9)."""

    def test_t10_incertains_et_corrompus(self):
        db = _db()
        _seed(db, 'asi1', terminal='finished', n=12)
        _seed(db, 'asi1', terminal='interrupted', n=8)
        _seed(db, 'asi1', terminal='etat-inconnu', n=3)
        obs, ignorees = competence_inputs.observations(db, TABLE)
        self.assertEqual(len(obs), 20)
        self.assertEqual(len(ignorees), 3)
        sortie = competence_inputs.classer_via_db(db, DEMANDE, table=TABLE)
        tracabilite = sortie['tracabilite']
        self.assertEqual(tracabilite['incertains'], 8)
        self.assertTrue(tracabilite['non_mesure'])
        db.close()


class T12EntreeCorrompueTest(unittest.TestCase):
    """T12 : champ manquant ou type faux => refus ENTREE_INVALIDE,
    aucune exception non rattrapee ; db None => REGISTRE_ABSENT."""

    def test_t12_demande_corrompue(self):
        db = _db()
        _seed(db, 'asi1')
        for demande in (None, {}, dict(DEMANDE, role=None),
                        dict(DEMANDE, sensible='oui'),
                        {'role': 'revue', 'mode': 'implementation'}):
            sortie = competence_inputs.classer_via_db(db, demande,
                                                      table=TABLE)
            self.assertIsNone(sortie['decision']['choisi'])
            self.assertEqual(sortie['refus']['raison'], 'ENTREE_INVALIDE')
        db.close()

    def test_t12_registre_absent(self):
        sortie = competence_inputs.classer_via_db(None, DEMANDE, table=TABLE)
        self.assertIsNone(sortie['decision']['choisi'])
        self.assertEqual(sortie['refus']['raison'], 'REGISTRE_ABSENT')


if __name__ == '__main__':
    unittest.main()
