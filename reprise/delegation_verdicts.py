"""Table delegation_verdicts (O1/L0) : verdicts E6, append-only, une ligne par request_key.

Seul E6 ecrit ici ; l'executant n'enregistre jamais son propre verdict.
Aucune connexion n'est creee par ce module : l'appelant fournit db (sqlite3).
setup() ne touche pas a PRAGMA user_version (il ne peut donc jamais le faire descendre).
"""
import json
import sqlite3
import time

TABLE = "delegation_verdicts"
VERDICTS = ("validé", "refusé", "incertain")
COLONNES = ("request_key", "verdict", "verdicteur", "base_sha", "cycles", "detail_json", "ts")

_DDL = (
    "CREATE TABLE IF NOT EXISTS delegation_verdicts ("
    "request_key TEXT PRIMARY KEY NOT NULL CHECK (length(request_key) > 0), "
    "verdict TEXT NOT NULL CHECK (verdict IN (%s)), "
    "verdicteur TEXT NOT NULL CHECK (length(verdicteur) > 0), "
    "base_sha TEXT, cycles INTEGER, detail_json TEXT, ts REAL NOT NULL)"
    % ",".join("'%s'" % v for v in VERDICTS),
    "CREATE TRIGGER IF NOT EXISTS delegation_verdicts_no_update "
    "BEFORE UPDATE ON delegation_verdicts "
    "BEGIN SELECT RAISE(ABORT, 'delegation_verdicts is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS delegation_verdicts_no_delete "
    "BEFORE DELETE ON delegation_verdicts "
    "BEGIN SELECT RAISE(ABORT, 'delegation_verdicts is append-only'); END",
)

_SEL = ("e.id AS id, e.ts AS ts, e.tool AS tool, e.channel AS channel, e.level AS level, "
        "e.terminal_state AS terminal_state, e.request_key AS request_key")


def setup(db):
    """Cree la table et les gardes append-only si absentes (idempotent)."""
    with db:
        for ddl in _DDL:
            db.execute(ddl)


def enregistrer(db, request_key, verdict, verdicteur, base_sha=None, cycles=None, detail=None):
    """Ajoute un verdict ; ValueError si entree invalide ou cle deja enregistree."""
    if not isinstance(request_key, str) or not request_key.strip():
        raise ValueError("request_key invalide")
    if not isinstance(verdict, str) or verdict not in VERDICTS:
        raise ValueError("verdict hors enum")
    if not isinstance(verdicteur, str) or not verdicteur.strip():
        raise ValueError("verdicteur invalide")
    if base_sha is not None and not isinstance(base_sha, str):
        raise ValueError("base_sha invalide")
    if cycles is not None and (type(cycles) is not int or cycles < 0):
        raise ValueError("cycles invalide")
    try:
        detail_json = None if detail is None else json.dumps(
            detail, sort_keys=True, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("detail non serialisable")
    setup(db)
    try:
        with db:
            db.execute(
                "INSERT INTO delegation_verdicts (%s) VALUES (?,?,?,?,?,?,?)" % ",".join(COLONNES),
                (request_key, verdict, verdicteur, base_sha, cycles, detail_json, time.time()))
    except sqlite3.IntegrityError:
        raise ValueError("verdict deja enregistre pour cette request_key")
    return lire(db, request_key)


def lire(db, request_key):
    """dict de la ligne, ou None si absente (table absente comprise)."""
    if not isinstance(request_key, str) or not request_key:
        return None
    try:
        row = db.execute("SELECT %s FROM delegation_verdicts WHERE request_key = ?"
                         % ",".join(COLONNES), (request_key,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    return None if row is None else dict(zip(COLONNES, tuple(row)))


def _existe(db, nom):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (nom,)).fetchone() is not None


def observations_avec_verdicts(db):
    """routing_events LEFT JOIN delegation_verdicts sur request_key ; verdict absent => None.

    Table verdicts absente => verdict/verdicteur None partout. routing_events absente => [].
    """
    if not _existe(db, "routing_events"):
        return []
    if _existe(db, TABLE):
        sql = ("SELECT %s, v.verdict AS verdict, v.verdicteur AS verdicteur "
               "FROM routing_events e LEFT JOIN delegation_verdicts v "
               "ON v.request_key = e.request_key ORDER BY e.id" % _SEL)
    else:
        sql = ("SELECT %s, NULL AS verdict, NULL AS verdicteur "
               "FROM routing_events e ORDER BY e.id" % _SEL)
    cur = db.execute(sql)
    noms = [d[0] for d in cur.description]
    return [dict(zip(noms, tuple(r))) for r in cur.fetchall()]
