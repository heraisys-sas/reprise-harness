"""Registre des chemins sensibles (O1/L4c) : validation et appariement fail-closed."""
import inspect
import json
import os
import re
import unicodedata
from functools import lru_cache

_CLES = {"schema", "version", "owner", "semantique", "regles"}
_CLES_REGLE = {"id", "classe", "glob"}
_SEMANTIQUE = {"non_liste": "non_sensible", "doute": "sensible"}
_KEBAB = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_META = frozenset("{}()[]|+^$")


def valide_registre(registre):
    """Liste d'erreurs (vide = valide)."""
    if not isinstance(registre, dict):
        return ["registre: objet attendu"]
    err = []
    ecart = set(registre) ^ _CLES
    if ecart:
        err.append("cles: ecart %s" % sorted(map(str, ecart)))
    if registre.get("schema") != "sensitive-paths@1":
        err.append("schema invalide")
    v = registre.get("version")
    if type(v) is not int or v != 1:
        err.append("version invalide")
    o = registre.get("owner")
    if not isinstance(o, str) or not o.strip():
        err.append("owner invalide")
    if registre.get("semantique") != _SEMANTIQUE:
        err.append("semantique invalide")
    regles = registre.get("regles")
    if not isinstance(regles, list) or not regles:
        err.append("regles: liste non vide attendue")
        return err
    vus = set()
    for i, r in enumerate(regles):
        if not isinstance(r, dict) or set(r) != _CLES_REGLE:
            err.append("regle %d: cles exactes id/classe/glob" % i)
            continue
        rid, classe, glob = r["id"], r["classe"], r["glob"]
        if not isinstance(rid, str) or not _KEBAB.fullmatch(rid):
            err.append("regle %d: id non kebab" % i)
        elif rid in vus:
            err.append("regle %d: id duplique %s" % (i, rid))
        else:
            vus.add(rid)
        if not isinstance(classe, str) or not classe.strip():
            err.append("regle %d: classe invalide" % i)
        if not isinstance(glob, str) or not glob:
            err.append("regle %d: glob invalide" % i)
        elif _META & set(glob):
            err.append("regle %d: meta-caractere regex dans glob" % i)
    return err


def charger_registre(chemin):
    """Lit et valide le registre ; ValueError si JSON invalide ou registre invalide."""
    with open(chemin, encoding="utf-8") as f:
        try:
            reg = json.load(f)
        except ValueError as e:
            raise ValueError("JSON invalide: %s" % e)
    err = valide_registre(reg)
    if err:
        raise ValueError("registre invalide: " + "; ".join(err))
    return reg


def _plie(s):
    s = unicodedata.normalize("NFC", s)
    return unicodedata.normalize("NFC", s.casefold())


def _home(home):
    return os.path.realpath(os.path.expanduser(home if home is not None else "~"))


def _normaliser_defaut(chemin, home=None):
    """realpath + NFC + casse ; symlink (ou ancetre) non resolvable => OSError."""
    base = _home(home)
    p = os.fspath(chemin)
    if not isinstance(p, str) or not p:
        raise ValueError("chemin invalide")
    if p == "~" or p.startswith("~/"):
        p = os.path.join(base, p[2:])
    elif not os.path.isabs(p):
        p = os.path.join(base, p)
    q = p
    while True:
        if os.path.islink(q) and not os.path.exists(q):
            raise OSError("symlink non resolvable")
        parent = os.path.dirname(q)
        if parent == q:
            break
        q = parent
    return _plie(os.path.realpath(p))


def _appeler(f, chemin, home):
    try:
        params = inspect.signature(f).parameters.values()
        n = sum(1 for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))
    except (TypeError, ValueError):
        n = 1
    return f(chemin, home) if n >= 2 else f(chemin)


def _formes(n, home):
    """(forme d'appariement, sous_home, forme absolue) du chemin normalise n."""
    bases = {_home(home), os.path.abspath(os.path.expanduser(home if home is not None else "~"))}
    n = _plie(n)
    if not os.path.isabs(n):
        n = os.path.normpath(n)
        if n == ".." or n.startswith("../"):
            raise ValueError("chemin relatif hors home")
        return n, True, _plie(os.path.join(_home(home), n))
    for b in sorted(map(_plie, bases), key=len, reverse=True):
        b = b.rstrip("/")
        if n == b:
            return "", True, n
        if n.startswith(b + "/"):
            return n[len(b) + 1:], True, n
    return n, False, n


@lru_cache(maxsize=256)
def _motif(glob):
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out), re.DOTALL)


def est_sensible(chemin, registre, normaliser=None, home=None):
    """True si une regle apparie ; tout doute ou exception => True (fail-closed)."""
    try:
        n = _appeler(normaliser, chemin, home) if normaliser else _normaliser_defaut(chemin, home)
        if not isinstance(n, str) or not n:
            return True
        forme, sous_home, absolu = _formes(n, home)
        for r in registre["regles"]:
            g = _plie(r["glob"])
            if g.startswith("~/"):
                if sous_home and _motif(g[2:]).fullmatch(forme):
                    return True
            elif g.startswith("/"):
                if _motif(g).fullmatch(absolu):
                    return True
            elif _motif(g).fullmatch(forme):
                return True
        return False
    except Exception:
        return True
