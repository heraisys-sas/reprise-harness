# Contrat E4 — taches E5 + gardes portees (Claude Desktop)

*Contrat du 26 novembre 2026 — request_key contrat-e4-taches-20261126-claude, chat ba05f0cb, question autoporteuse (etat R8 rev.3 + reponses Q1-Q6 + E2 citee inlines), sans piece jointe. Reponses Q1-Q6 proposees par ASI1 en autonomie deleguee, revisables par Olivier.*

# contrat-e4-taches-20261126.md

Contrat de tâche d'exécution (étage E4) · version `contrat-e4-taches-20261126@1.0` · statut : spécification, aucun code · auteur : Claude (rôle architecture/contrats) · révisable par l'opérateur.

**Lecture de l'E2 fournie.** Je la prends comme donnée (reprise/routing_events.py l.40-61 @3863241 ; backend_registry.BACKENDS @bc32055 l.29-60) : je ne l'ai pas relue dans le dépôt. Elle sera revérifiée par `verify_citation` (O5) avant E4.

**Vocabulaire (Q7).** La demande parle de « contrat de tâche E5 » et d'« acteur E5 », alors que le contrat décrit la tâche exécutée à l'étage E4. J'utilise : *tâche* = unité exécutée en E4 ; *auteur* = acteur qui produit le diff ; *vérificateur* = acteur des tests indépendants ; *intégrateur* = acteur E7. Confirmer que « acteur E5 » désigne bien l'auteur.

## 1. Schéma des champs

Légende : **O1** = émis par le profil O1, validé (jamais décidé) par l'admission ; **calc** = calculé par l'admission, jamais fourni par l'appelant ; **+** = ajout au rappel, à confirmer (Q8).

| Champ | Type | Oblig. | Défaut | Garde associée |
|---|---|---|---|---|
| contract_version | const | oui | — | égalité stricte ; additionalProperties=false |
| request_key | str ≤128 | oui | — | recalculée = `ct-` + 32 premiers hex de sha256 du JSON canonique (tous champs sauf request_key, retry_of, ttl_s) ; ≠ ⇒ refus |
| attempt + | int ≥1 | oui | 1 | entre dans le digest : un retry change donc la clé sans collision d'identité |
| retry_of | str\|null | si attempt>1 | null | réconciliation (§6) ; absent si attempt=1 |
| plan_id | str | oui | — | plan existant, approuvé, digest épinglé (§4) |
| lot_id + | str | oui | — | ∈ plan.lots |
| task_n | int | oui | — | unique dans le lot |
| depends_on | [request_key] | non | [] | acyclique ; chaque dépendance terminale vérifiée avant dispatch |
| objective | str ≤280 | oui | — | une phrase ; donnée, jamais lue par le routage |
| role | enum mass_code\|impl\|revue\|analyse | oui | — | **O1** ; matrice (role, actor) |
| actor | enum (registre) | oui | — | **O1** ; matrice dérivée du registre ; forbidden_roles |
| mode | expertise\|implementation | oui | — | **O1** ; implementation ⇒ workspace_write du registre + consent_ref |
| billing_class | free\|included\|paid-named | oui | — | **O1** ; ≠ unknown ; paid-named ⇒ consent_ref nommant le backend |
| consent_ref | str\|null | si paid-named ou implementation | null | **O1** transmet, ne crée jamais ; consentement lié au digest du contrat |
| o1_ref + | {spec, digest_entree} | oui | — | traçabilité de la décision O1 ; digest recalculé |
| e2_ref | [fichier#Ldeb-Lfin@base_sha] ≥1 | oui | — | `verify_citation` : fichier, lignes, base_sha hex 40 |
| base_sha | hex 40 | oui | — | = base du plan ; sinon périmé ⇒ refus |
| context_paths | [{path, sha256}] | oui | — | digest recalculé sur l'arbre @base_sha ; ≤ N chemins ; sensibles contrôlés (§3) |
| allowed_paths | [str] ≥1 | oui | **aucun** | liste blanche fail-closed ; absent/vide ⇒ refus (§2.6) |
| prohibited | [str] | non | [] | complément ; s'ajoute à l'interdit implicite (§5.2) |
| acceptance | [{critere, verif}] ≥1 | oui | — | verif = `test:<nom>` ou `cmd:<runner allowlisté + args>` ; shell libre refusé |
| output_form | patch_unifie\|rapport_borne | oui | — | patch ⇒ passe par la garde de périmètre (§5) |
| budget.wall_clock_s | int | oui | — | dépassement ⇒ état `expire` |
| budget.max_diff_files / max_diff_lines | int | oui (si patch) | — | plafonds vérifiés sur le diff réel |
| budget.max_parallel | int ≥1 | oui | 1 | mass_code seulement ; écritures disjointes (§4) |
| budget.quota_ceiling | {source:"populate_from_quota", max_pct} | oui | — | le contrat peut abaisser, jamais relever, le plafond système ; relu au dispatch |
| budget.ttl_s | int | oui | — | expiration de la validité du contrat |
| budget.max_output_bytes / max_turns | int | oui | — | refus fail-closed, pas de troncature silencieuse |
| sensitive | bool | **calc** | — | calculé par §3 ; tout `sensitive` fourni par l'appelant ⇒ refus |

**Garanties du schéma.** (a) Aucun champ de prose n'entre dans le routage ; le routage ne lit que enums, entiers et digests (anti-injection). (b) role, actor, mode, billing_class, consent_ref : l'admission les **compare** à la sortie O1 (o1_ref) et refuse toute divergence ; elle ne les choisit jamais.

## 2. Gardes à l'admission

Évaluées dans cet ordre, **fail-fast** : le premier échec est le seul renvoyé (déterminisme).

0. **Structure et identité** : schéma strict ; request_key recalculée ; si la clé existe déjà avec le même digest ⇒ **rejeu idempotent** (rend le reçu existant, ne lance rien) ; même clé, digest différent ⇒ `IDENTITE_CONFLIT`.
1. **Politique des rôles** : (role, actor) ∈ matrice ; role ∉ forbidden_roles du registre.
2. **Registre / mode / billing** : entrée de capacité présente pour le mode ; billing ≠ unknown ; workspace_write si implementation ; paid-named ⇒ consent_ref.
3. **Admissibilité** : `routing_events.admissible` (route_admissibility.identity_status = known ; route_refusals ; vetos) ; unknown ⇒ refus.
4. **plan_id** : présent, approuvé, lot_id valide, base_sha identique.
5. **e2_ref / base_sha** : `require_cited_evidence` ; digests de context_paths recalculés.
6. **allowed_paths** : présent, non vide, sans `..`, sans chemin absolu, sans `**` à la racine, ⊆ allowed_paths du lot.
7. **sensitive_paths** : voir §3.
8. **Budget** : tous champs présents et ≤ plafonds du lot ; quota_ceiling relu.

**Message de refus standard** (JSON, aucune prose libre) :
`{"garde":"<CODE>","etape":<0-8>,"champ":"<nom>","recu":"<valeur ou digest, ≤80 car.>","attendu":"<règle>"}`.
Pour un échec sensible, `recu` = classe de la règle, jamais le contenu.
Aucun refus ne déclenche de repli sur un autre actor ou une autre classe de facturation.

## 3. Chemins sensibles (Q4)

**Fichier** : `contracts/sensitive-paths.v1.txt`, versionné dans le dépôt. **Propriétaire** : Olivier. **Une règle par ligne**, format `classe<TAB>glob` ; `#` = commentaire ; pas de regex (ambiguïté, coût). Glob : `*`, `**`, `?`, sans négation.
Contenu initial : `cle-ssh ~/.ssh/**` · `cle-pem **/*.pem` · `cle-key **/*.key` · `env **/.env` · `env **/.env.*` · `trousseau **/*keychain*` · `jeton **/*token*` · `cookie **/*cookie*`.

**Normalisation avant appariement** : realpath (symlinks résolus), `~` expansé, NFC, **insensible à la casse** (macOS est insensible à la casse par défaut), chemins relatifs rapportés à la racine du dépôt.
**Fail-closed** : chemin non résoluble, symlink sortant du dépôt, appariement ambigu ⇒ traité **sensible**. Non listé = non sensible.

**Règle mécanique.** Si ∃ p ∈ context_paths ∪ allowed_paths ∪ (chemins du diff) tel que p apparie une règle ⇒ `sensitive=true` (calculé) ⇒ **actor ≠ metaai** (refus `SENSIBLE_ACTOR_INTERDIT`).
**Q9 (hors décision silencieuse)** : demandé = « ≠ metaai » ; je recommande d'étendre à « actor ∈ acteurs locaux sans sortie réseau (IronClaw) », car un secret envoyé à ChatGPT, Claude Desktop ou Codex sort aussi de la machine. Décision opérateur.
**Révision** : toute modification de la liste = commit sur dev, go d'Olivier ; ajout libre, retrait avec justification écrite dans le commit ; la liste est lue au base_sha du plan (pas de liste « vivante » qui change sous une tâche en cours).

## 4. Format du plan E4

Fichier `plans/<plan_id>.json`, immuable une fois approuvé. **plan_id** : `<sujet>-<yyyymmdd>[-vN]`. Changer le plan = nouvelle version `-vN`, nouvelle approbation.

```
{plan_id, version, base_sha, e2_refs[], approuve_par, digest,
 lots:[{lot_id, branche, objectif (1 phrase), allowed_paths[], prohibited[],
        depends_on[lot_id], acceptance[], max_cycles}]}
```

- **Un lot = une branche = un objectif.** Un lot à deux objectifs est refusé à l'approbation.
- **Indépendance vérifiée, non déclarée** : deux lots sans relation depends_on doivent avoir des allowed_paths **disjoints** ; sinon refus. Même règle pour max_parallel > 1 dans un lot.
- **Pas de tâche sans plan_id** : étape 4 de l'admission ; une tâche dont le lot n'existe pas, ou dont le plan n'est pas approuvé, est refusée. Aucune voie ne l'exempte, y compris la voie courte (§6).
- **Q10** : qui approuve un plan ? Proposé : l'opérateur pour la voie normale ; politique écrite pour « E3 lite ». À confirmer.

## 5. Garde de périmètre du diff (pré-garde, avant E5)

**Statut** : garde machine déterministe de E4, **portée par le contrat** (allowed_paths, prohibited, budgets). Aucun modèle ne la franchit ni ne l'évalue. Elle s'exécute avant les tests : un diff hors périmètre n'est jamais testé.

**5.1 Calcul.** Diff entre base_sha et le résultat, avec **renommages désactivés** (`--no-renames`) : un renommage apparaît comme suppression de l'ancien chemin + ajout du nouveau, et **les deux** doivent être dans le périmètre. Entrées en NUL-séparé. Chaque chemin est normalisé comme en §3.

**5.2 Règles (toutes requises).**
1. Chaque chemin touché (ajout, modification, suppression, renommage, changement de mode) ∈ allowed_paths.
2. Aucun chemin ∈ prohibited **ni** dans l'interdit implicite de la garde : `.git/**`, `VERSION`, `CHANGELOG*` (règle opérateur : hors release), `.github/**`, `.gitlab-ci.yml`, `ci_test.py`, la liste sensible. **Les tests indépendants** ne sont pas un interdit de la garde : contrainte E4 — les `allowed_paths` du lot ne les déclarent pas (SOD : l'auteur ne modifie pas ce qui le vérifie), et la garde refuse toute entrée hors `allowed_paths`.
3. Aucun symlink créé ou modifié, aucun sous-module, aucun fichier binaire, sauf autorisation nominative dans allowed_paths.
4. Nombre de fichiers ≤ max_diff_files ; lignes ajoutées + supprimées ≤ max_diff_lines.
5. sensitive=true recalculé sur le diff : même règle d'actor que §3.

**5.3 Hors périmètre ⇒ refus net.** État `refuse_perimetre`. Le diff entier est **rejeté** ; il n'est ni tronqué, ni appliqué partiellement, ni « nettoyé » des chemins en trop. Seul son digest est conservé comme preuve. Retour à E4 comme échec nommé.
Message : `{"garde":"PERIMETRE_DIFF","etape":"pre-E5","champ":"diff","recu":{"n_hors":N,"exemples":[≤5 chemins]},"attendu":"diff ⊆ allowed_paths, hors interdit"}`.

**5.4 SOD machine.** auteur ≠ vérificateur (tests indépendants) ≠ intégrateur E7 ; E6 ≠ auteur. Refus à l'admission si la matrice du plan viole cette égalité.

## 6. Bornes de cycles

- **Par lot** : `max_cycles` (défaut 3, plafond dur 5). Chaque E5 rouge consomme un cycle. À épuisement : état `bloque_operateur` ; **aucune clé neuve** sans décision de l'opérateur.
- **Retry = clé neuve + réconciliation.** `attempt`+1 et `retry_of=<clé précédente>` obligatoires. Le reçu précédent doit être en état **connu** (`echec_verifie`, `refuse_*`, `expire` avec preuve de non-livraison) ; en état `incertain`, seule une **réconciliation explicite de l'opérateur** autorise la clé neuve (« arrivé ≠ traité », jamais de réémission automatique).
- **Plafond de plan** : total de tentatives ≤ 3 × nombre de lots ; au-delà, plan `bloque_operateur`.
- **ttl_s** : à expiration, état `expire` ; compté comme cycle ; un `expire` sans preuve de non-livraison est traité comme `incertain`.
- **Voie courte [PROPORTION]** : E2 automatique → E3 lite → E4 → E5 → E6 → E8. Éligibilité **mécanique** (jamais déclarée) : un seul lot, ≤ 2 fichiers, ≤ 60 lignes, aucun chemin sensible, aucune dépendance ajoutée, mode compatible. **E3 lite** = un plan à un lot, toujours enregistré : plan_id reste obligatoire. `max_cycles` = 2. Si le diff réel dépasse ces bornes, la voie est invalidée ⇒ refus, pas de passage silencieux à la voie normale.
- **Q11** : la voie courte s'arrête à E8, sans E7 ; l'intégration (FF dev) reste-t-elle un acte opérateur hors pipeline ? Je le suppose.

## 7. Critères falsifiables (cas adverses)

| # | Cas | Verdict attendu |
|---|---|---|
| C1 | Tâche valide en tout point, sans `plan_id` | Refus étape 4, `PLAN_ID_ABSENT` ; aucun envoi ; aucun reçu d'admission créé. Si le plan existe mais n'est pas approuvé : `PLAN_NON_APPROUVE`. |
| C2 | `allowed_paths` absent ou `[]` | Refus étape 6, `ALLOWED_PATHS_ABSENT`. Interdit de retomber sur context_paths ou « tout le dépôt ». |
| C3 | context_paths contient `.env` (ou symlink non résoluble), actor = metaai | `sensitive=true` calculé, refus étape 7, `SENSIBLE_ACTOR_INTERDIT` ; même verdict si l'appelant déclare `sensitive:false` (champ calculé : refus `CHAMP_CALCULE_FOURNI`). |
| C4 | Diff touchant un fichier hors allowed_paths (ex. `VERSION`), ou renommage d'un fichier autorisé vers un chemin non autorisé | `refuse_perimetre`, `PERIMETRE_DIFF` ; diff rejeté en entier, jamais tronqué ; les tests ne sont pas lancés. |
| C5 | `attempt=2`, `retry_of=K` où K est `incertain` (ou `retry_of` absent) | Refus étape 0, `RETRY_NON_RECONCILIE`. Même contenu et même attempt ⇒ rejeu idempotent, aucun envoi. |
| C6 | Deux lots non liés, allowed_paths qui se recouvrent | Plan refusé à l'approbation, `LOTS_NON_DISJOINTS`. |
| C7 | Diff modifiant un test indépendant, ou auteur = vérificateur | Refus, `SOD_VIOLEE`. |
| C8 | Même entrée soumise deux fois | Même verdict, même JSON d'erreur (déterminisme). |

Critère de recette : C1–C8 verts, et aucun test ne peut être satisfait en tronquant ou en élargissant implicitement un périmètre.

## 8. Conséquences sur la spec O1 (erratum à traiter, hors de ce contrat)

D'après l'E2 fournie : role et kind **ne sont pas des colonnes** de `routing_events` ; durée et quota non plus ; `terminal_state='dispatched'` n'est pas un succès. Donc la spec O1 §1.2 doit (a) dériver role/kind par une fonction versionnée à partir de tool + level + routes JSON, et classer `non_mesure` toute observation non dérivable ; (b) compter comme « succès vérifié » uniquement ce que E5 a validé. **Q12** : où est enregistré le verdict E5/E8 pour que O1 le lise (nouvel événement dans routing_events, ou table compagnon keyée par request_key) ? Sans cela O1 ne peut mesurer aucun succès : à trancher avant O1/E4.

## Questions opérateur (aucun choix silencieux)

Q7 « acteur E5 » = auteur ? · Q8 ajouts au rappel : `attempt`, `lot_id`, `o1_ref` · Q9 sensible ⇒ « actor ≠ metaai » seulement, ou acteurs locaux uniquement ? · Q10 qui approuve un plan, et « E3 lite » ? · Q11 voie courte sans E7 ? · Q12 enregistrement du verdict E5/E8.
## 9. Réponses aux questions du contrat (ASI1, autonomie déléguée, révisables par Olivier)

- **Q7** : CONFIRMÉ — « acteur E5 » désigne l'auteur du diff. Vocabulaire adopté : tâche / auteur / vérificateur / intégrateur.
- **Q8** : CONFIRMÉ — les champs ajoutés (contract_version, attempt, lot_id, task_n, objective) sont acceptés tels que proposés.
- **Q9** : ADOPTÉ — sensible ⇒ acteur sans sortie réseau (local), pas seulement « ≠ metaai ». Exception unique : consent_ref nominatif de l'opérateur, tracé.
- **Q10** : ADOPTÉ tel que proposé — l'opérateur approuve la voie normale ; une politique écrite approuve la voie E3 lite.
- **Q11** : CONFIRMÉ — la voie courte s'arrête à E8 sans E7 ; le FF de `dev` reste un acte opérateur hors pipeline (règle : go explicite).

## 10. Plan E4 du premier lot (établi par ASI1 après contrat)

- plan_id : `garde-perimetre-20261126` (version 1, base_sha = 3863241, approuve_par : opérateur via autonomie déléguée).
- Un lot : garde de périmètre du diff (contrat §5) — module `reprise/perimetre_guard.py` + tests `reprise/tests/test_perimetre_guard.py` seulement.
- allowed_paths : `reprise/perimetre_guard.py`, `reprise/tests/test_perimetre_guard.py`. prohibited : tout le reste (implicite §5.2.2 inclus).
- acceptance : C1–C5 du contrat §7 verts ; diff hors périmètre ⇒ refuse_perimetre avec preuve digest ; renommage = deux chemins contrôlés ; budgets max_diff_files/max_diff_lines respectés.
- max_cycles : 3. SOD : auteur = Meta AI, vérificateur = ASI1 (suite locale), intégrateur = opérateur.
- **Q12** : TRANCHÉ — table compagnon `delegation_verdicts(request_key CLE, stage, verdict, verified_by, ts, digest_preuve)` alimentée par E5/E6/E8 ; O1 joint `routing_events ⋈ delegation_verdicts` sur request_key. Motif : le CHECK(type IN routed/explicit/routing_substituted) de SCHEMA_V2 (@3863241) interdit un nouveau type sans migration ; le verdict est un fait d'exécution, pas un fait de routage ; l'ajout est additif. L'intention de la revue (« les résultats d'étage alimentent les mesures ») est honorée par la jointure.

**Erratum (26 novembre 2026, revue E6b — spec WORKFLOW-REPRISE v1.2 §3/E8).** L'écrivain de `delegation_verdicts` est **E6 seul** (append-only, keyé par request_key) : la mention « alimentée par E5/E6/E8 » ci-dessus est caduque — l'exécutant n'écrit jamais son propre verdict (anti-injection). Numérotation interne : les étages « E4/E5 » de ce contrat suivent l'ancien pipeline R7 (E4 = exécution, E5 = vérification) ; la table de correspondance normative est en spec v1.2 §0.
