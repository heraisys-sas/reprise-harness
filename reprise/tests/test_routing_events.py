import json
import sqlite3
import weakref
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import external_rankings
import routing_events


def memory_db(owner, version=0, legacy=False):
    db = sqlite3.connect(':memory:')
    owner.addCleanup(db.close)
    if legacy:
        # Table v1 (lot ①, pré-migration) avec une ligne de test.
        db.execute('CREATE TABLE routing_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, '
                   "type TEXT, tool TEXT, channel TEXT, requested_route TEXT, served_route TEXT, "
                   'level TEXT, terminal_state TEXT, request_key TEXT, thread_hash TEXT, '
                   'loop_signature TEXT, reason TEXT)')
        db.execute("INSERT INTO routing_events VALUES (1, 0, 'routed', 'legacy', 'codex', NULL, NULL, "
                   "NULL, 'dispatched', NULL, 'oldhash', NULL, 'v1 row')")
    db.execute(f'PRAGMA user_version={version}')
    routing_events.setup(db)
    return db


class RoutingEventsTests(unittest.TestCase):
    def test_setup_migrates_v1_to_v2_idempotently(self):
        db = memory_db(self, version=0, legacy=True)
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],
                         max(routing_events.VERSION, external_rankings.VERSION))
        # La ligne v1 est perdue (recréation assumée) et le schéma v2 est en place.
        self.assertEqual(db.execute('SELECT COUNT(*) FROM routing_events').fetchone()[0], 0)
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='routing_events'")}
        self.assertIn('routing_events_ts', indexes)
        self.assertIn('routing_events_type', indexes)
        # Deuxième setup : no-op, aucune perte.
        routing_events.setup(db)
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],
                         max(routing_events.VERSION, external_rankings.VERSION))

    def test_setup_rejects_unknown_future_version(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute(f'PRAGMA user_version={max(routing_events.VERSION, external_rankings.VERSION) + 1}')
        with self.assertRaises(ValueError):
            routing_events.setup(db)

    def test_migration_v2_to_v3_additive_preserves_data(self):
        db = memory_db(self, version=0, legacy=True)
        # Simule une base v2 (lot ① bis) : une donnée existante, sans les colonnes v3.
        db.execute("INSERT INTO routing_events(ts,type,tool,channel,terminal_state,reason) "
                   "VALUES (0,'routed','t','codex','dispatched','before upgrade')")
        db.execute('ALTER TABLE routing_events DROP COLUMN account_key')
        db.execute('ALTER TABLE routing_events DROP COLUMN admissibility')
        db.execute('PRAGMA user_version=2')
        routing_events.setup(db)
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],
                         max(routing_events.VERSION, external_rankings.VERSION))
        columns = {row[1] for row in db.execute('PRAGMA table_info(routing_events)')}
        self.assertIn('account_key', columns)
        self.assertIn('admissibility', columns)
        self.assertEqual(db.execute(
            'SELECT admissibility FROM routing_events WHERE reason=?', ('before upgrade',)).fetchone()[0],
            'not_required')

    def test_legacy_opaque_identity_scrub_preserves_receipts_and_existing_valid_keys(self):
        db = memory_db(self)
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],
                         max(routing_events.VERSION, external_rankings.VERSION))
        # Simulate a pre-scrub database already marked with the newest schema.
        db.execute('DELETE FROM routing_meta WHERE key=?',
                   (routing_events.LEGACY_IDENTITY_SCRUB_KEY,))
        events = [
            (101, 'legacy-account-identifier', 'finished'),
            (102, 'A' * 64, 'blocked'),
            (103, 'f' * 64, 'finished'),
            (104, None, 'dispatched'),
        ]
        for ident, account, state in events:
            db.execute(
                'INSERT INTO routing_events(id,ts,type,tool,channel,terminal_state,'
                'request_key,thread_hash,account_key) VALUES (?,?,?,?,?,?,?,?,?)',
                (ident, float(ident), 'explicit', 'reprise_delegate', 'codex',
                 state, f'request-{ident}', 'thread-pseudonym', account))
        rows = [
            ('codex', 'legacy-plain-account', 'f' * 64, 'included', 'known', 100.0),
            ('custom', 'e' * 64, 'legacy-plain-pool', 'included', 'known', 101.0),
            ('verified', 'a' * 64, 'b' * 64, 'included', 'known', 102.0),
            ('local', None, None, 'free', 'known', 103.0),
        ]
        db.executemany('INSERT INTO route_admissibility VALUES (?,?,?,?,?,?)', rows)
        db.commit()

        routing_events.setup(db)
        result = db.execute(
            'SELECT id,ts,terminal_state,request_key,thread_hash,account_key '
            'FROM routing_events ORDER BY id').fetchall()
        self.assertEqual(result, [
            (101, 101.0, 'finished', 'request-101', 'thread-pseudonym', None),
            (102, 102.0, 'blocked', 'request-102', 'thread-pseudonym', None),
            (103, 103.0, 'finished', 'request-103', 'thread-pseudonym', 'f' * 64),
            (104, 104.0, 'dispatched', 'request-104', 'thread-pseudonym', None),
        ])
        self.assertEqual(db.execute(
            'SELECT channel,account_key,pool_key,billing_class,identity_status,updated_at '
            'FROM route_admissibility ORDER BY channel').fetchall(), [
            ('codex', None, None, 'unknown', 'unknown', 100.0),
            ('custom', None, None, 'unknown', 'unknown', 101.0),
            ('local', None, None, 'free', 'known', 103.0),
            ('verified', 'a' * 64, 'b' * 64, 'included', 'known', 102.0),
        ])
        self.assertEqual(routing_events.admissible(db, 'codex', automatic=True),
                         (False, 'unknown'))
        marker = db.execute('SELECT value FROM routing_meta WHERE key=?',
                            (routing_events.LEGACY_IDENTITY_SCRUB_KEY,)).fetchone()[0]
        self.assertEqual(json.loads(marker),
                         {'events_redacted': 2, 'admissibility_revoked': 2})
        self.assertNotIn('legacy', marker)
        routing_events.setup(db)
        self.assertEqual(db.execute('SELECT value FROM routing_meta WHERE key=?',
                                    (routing_events.LEGACY_IDENTITY_SCRUB_KEY,)).fetchone()[0],
                         marker)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM routing_events').fetchone()[0], 4)

    def test_legacy_identity_scrub_rolls_back_all_redactions_on_error(self):
        db = memory_db(self)
        db.execute('DELETE FROM routing_meta WHERE key=?',
                   (routing_events.LEGACY_IDENTITY_SCRUB_KEY,))
        db.execute(
            "INSERT INTO routing_events(ts,type,tool,channel,terminal_state,account_key) "
            "VALUES (1,'explicit','t','codex','finished','legacy-account')")
        db.execute("INSERT INTO route_admissibility VALUES "
                   "('codex','legacy-account',NULL,'included','known',100)")
        db.execute('''CREATE TRIGGER refuse_scrub BEFORE UPDATE ON route_admissibility
                      BEGIN SELECT RAISE(ABORT, 'synthetic scrub failure'); END''')
        db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'synthetic scrub failure'):
            routing_events.setup(db)
        # The update to routing_events happened first but cannot survive failure.
        self.assertEqual(db.execute(
            'SELECT account_key FROM routing_events').fetchone()[0], 'legacy-account')
        self.assertEqual(db.execute(
            'SELECT billing_class FROM route_admissibility').fetchone()[0], 'included')
        self.assertIsNone(db.execute('SELECT value FROM routing_meta WHERE key=?',
                                    (routing_events.LEGACY_IDENTITY_SCRUB_KEY,)).fetchone())
        db.execute('DROP TRIGGER refuse_scrub')
        db.commit()
        routing_events.setup(db)
        self.assertIsNone(db.execute('SELECT account_key FROM routing_events').fetchone()[0])
        self.assertEqual(db.execute(
            'SELECT account_key,pool_key,billing_class,identity_status '
            'FROM route_admissibility').fetchone(), (None, None, 'unknown', 'unknown'))
        self.assertIsNotNone(db.execute('SELECT value FROM routing_meta WHERE key=?',
                                       (routing_events.LEGACY_IDENTITY_SCRUB_KEY,)).fetchone())

    def test_admissibility_matrix(self):
        db = memory_db(self)
        for channel, billing in (('codex', 'included'), ('claude-api', 'paid'),
                                 ('foundation-models', 'free'), ('new-route', 'unknown')):
            routing_events.declare(db, channel, billing)
        # automatic=True : free/included passent ; paid et unknown refusés (fail-closed).
        self.assertEqual(routing_events.admissible(db, 'codex', automatic=True), (True, 'included'))
        self.assertEqual(routing_events.admissible(db, 'foundation-models', automatic=True), (True, 'free'))
        self.assertEqual(routing_events.admissible(db, 'claude-api', automatic=True), (False, 'paid'))
        self.assertEqual(routing_events.admissible(db, 'new-route', automatic=True), (False, 'unknown'))
        self.assertEqual(routing_events.admissible(db, 'never-declared', automatic=True), (False, 'unknown'))
        # automatic=False (route explicite) : toléré quelle que soit la classe.
        self.assertEqual(routing_events.admissible(db, 'claude-api', automatic=False), (True, 'paid'))
        self.assertEqual(routing_events.admissible(db, 'new-route', automatic=False), (True, 'unknown'))

    def test_populate_from_quota_requires_known_identity_and_allowed(self):
        db = memory_db(self)
        routing_events.populate_from_quota(db, {'identity_status': 'known', 'account_key': 'hmac-1',
                                                'ordinary_usage_allowed_observed': True,
                                                'pools': [{'quota_pool_key': 'pool-1'}]})
        row = db.execute("SELECT billing_class,account_key,pool_key FROM route_admissibility WHERE channel='codex'").fetchone()
        self.assertEqual(row, ('included', 'hmac-1', 'pool-1'))
        routing_events.populate_from_quota(db, {'identity_status': 'known', 'account_key': 'hmac-1',
                                                'ordinary_usage_allowed_observed': False, 'pools': []})
        row = db.execute("SELECT billing_class FROM route_admissibility WHERE channel='codex'").fetchone()
        self.assertEqual(row[0], 'unknown')
        routing_events.populate_from_quota(db, {'identity_status': 'unknown', 'account_key': None})
        row = db.execute("SELECT billing_class FROM route_admissibility WHERE channel='codex'").fetchone()
        self.assertEqual(row[0], 'unknown')

    def test_seed_local_declares_free_channels_once(self):
        db = memory_db(self)
        routing_events.seed_local(db)
        routing_events.seed_local(db)
        rows = db.execute("SELECT channel,billing_class FROM route_admissibility WHERE channel IN ('foundation-models','ironclaw')").fetchall()
        self.assertEqual(sorted(rows), [('foundation-models', 'free'), ('ironclaw', 'free')])
        routing_events.declare(db, 'ironclaw', 'unknown')
        routing_events.seed_local(db)
        row = db.execute("SELECT billing_class FROM route_admissibility WHERE channel='ironclaw'").fetchone()
        self.assertEqual(row[0], 'unknown')

    def test_routed_event(self):
        db = memory_db(self)
        event_id = routing_events.record(db, 'routed', 'reprise_delegate', 'codex',
                                         served={'model': 'gpt-6-sol', 'effort': 'medium'},
                                         level='standard', request_key='rk-1', thread_id='sess-123')
        row = routing_events.recent(db)[0]
        self.assertEqual(row['type'], 'routed')
        self.assertIsNone(row['requested_route'])
        self.assertIn('gpt-6-sol', row['served_route'])
        self.assertEqual(row['terminal_state'], 'dispatched')
        routing_events.finalize(db, event_id, 'finished')
        self.assertEqual(routing_events.recent(db)[0]['terminal_state'], 'finished')

    def test_explicit_event(self):
        db = memory_db(self)
        routing_events.record(db, 'explicit', 'reprise_delegate', 'codex',
                              requested={'model': 'gpt-6-astra'}, served={'model': 'gpt-6-astra', 'effort': 'high'},
                              level='deep')
        row = routing_events.recent(db)[0]
        self.assertEqual(row['type'], 'explicit')
        self.assertIn('gpt-6-astra', row['requested_route'])

    def test_routing_substituted_type_accepted_and_thread_pseudonymized(self):
        db = memory_db(self)
        # La substitution est gelée : le type est journalisable par le schéma,
        # mais aucun code de production ne le produit aujourd'hui.
        routing_events.record(db, 'routing_substituted', 'reprise_delegate', 'codex',
                              requested={'model': 'gpt-6-astra'}, served={'model': 'gpt-6-sol'},
                              thread_id='01a1418b-f417-7be0-b62b-25c63629a6f2', loop_signature='ls-abc')
        row = routing_events.recent(db)[0]
        self.assertEqual(row['type'], 'routing_substituted')
        self.assertEqual(len(row['thread_hash']), 16)
        self.assertNotIn('01a1418b', row['thread_hash'])

    def test_routing_substituted_requires_both_routes(self):
        db = memory_db(self)
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO routing_events(ts,type,tool,channel,terminal_state) "
                       "VALUES (0,'routing_substituted','t','codex','dispatched')")

    def test_hmac_stable_across_reopens_with_same_salt(self):
        db = memory_db(self)
        routing_events.record(db, 'routed', 't', 'codex', thread_id='stable-session')
        first = routing_events.recent(db)[0]['thread_hash']
        # Même sal (copié depuis routing_meta) → même HMAC ; le sal est persistant, pas aléatoire par ouverture.
        db2 = sqlite3.connect(':memory:')
        self.addCleanup(db2.close)
        db2.execute('CREATE TABLE routing_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        for key, value in db.execute('SELECT key,value FROM routing_meta'):
            db2.execute('INSERT INTO routing_meta VALUES (?,?)', (key, value))
        self.assertEqual(routing_events.pseudonymize(db2, 'stable-session'), first)

    def test_hmac_differs_across_salts(self):
        db1, db2 = memory_db(self), memory_db(self)
        self.assertNotEqual(routing_events.pseudonymize(db1, 'x'), routing_events.pseudonymize(db2, 'x'))

    def test_purge_explicit_only(self):
        db = memory_db(self)
        routing_events.record(db, 'routed', 't', 'codex')
        db.execute('UPDATE routing_events SET ts=1 WHERE id=(SELECT MAX(id) FROM routing_events)')
        routing_events.record(db, 'routed', 't', 'codex')
        self.assertEqual(routing_events.purge(db, before=100), 1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM routing_events').fetchone()[0], 1)
        self.assertEqual(routing_events.retention_days(db), routing_events.DEFAULT_RETENTION_DAYS)

    def test_rejects_unknown_type_and_non_dict_route(self):
        db = memory_db(self)
        with self.assertRaises(ValueError):
            routing_events.record(db, 'invented', 'tool', 'codex')
        with self.assertRaises(ValueError):
            routing_events.record(db, 'routed', 'tool', 'codex', requested='flash')

    def test_no_prompt_content_stored(self):
        db = memory_db(self)
        routing_events.record(db, 'routed', 'reprise_delegate', 'claude-desktop',
                              served={'backend': 'claude-desktop'}, reason='Default route, no model requested')
        payload = str(routing_events.recent(db)[0])
        self.assertNotIn('/Users/', payload)


if __name__ == '__main__':
    unittest.main()
