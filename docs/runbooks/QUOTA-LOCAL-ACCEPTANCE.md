# Acceptation locale — continuation après quota et propriété des sessions

**Destinataires :** agents techniquement compétents, autorisés à sélectionner les
outils de diagnostic et à proposer des correctifs dans le périmètre d'une issue.
La conduite technique du test leur appartient ; **les décisions architecturales,
les accès à une session réelle et les changements de route propriétaire exigent
une preuve explicite et, lorsqu'indiqué, le go opérateur**.
Ce document fixe les invariants, la checklist reproductible et la forme du rapport.
Une hypothèse contradictoire doit être notée comme `NOT_VERIFIED`, jamais résolue
par intuition ou par substitution implicite de route.
Contrat métier : [QUOTA-CONTINUATION-CONTRACT](../contracts/QUOTA-CONTINUATION-CONTRACT.md).

## Ce que l'agent doit livrer

Un rapport expurgé qui répond à quatre questions :

1. Une interruption **réelle** a-t-elle été observée et attribuée au bon
   harness/instance/chat (ou seulement un candidat) ?
2. L'événement reste-t-il enregistré, même avec quota actuel `unknown` ?
3. À l'échéance, une continuation **native dans la même session** a-t-elle
   été effectivement tentée et reconnue ? Si non, quel verrou factuel manque ?
4. Les tests locaux et CI passent-ils, et quel SHA précis contient les
   changements ? Aucune réussite métier déduite d'un accusé technique.

Ne pas annoncer de scénario testé lorsque seules des fixtures ont été exécutées.

## Précautions impératives

- **Ne jamais arrêter, redémarrer ni reconfigurer `codexd`**.
  Ne pas fermer ni relancer ChatGPT Desktop ou ReClaude sauf permission
  **explicite et spécifique** de l'opérateur pour une étape de test.
- Pas d'API Accessibilité macOS, de modification de bundle signé, de
  copie de cookies/tokens, d'extraction de conversations, de désactivation
  des permissions ou de suppression de verrous.
- Ne jamais envoyer `turn/start`, `thread/resume`,
  `thread/queue/start`, ni de message de continuation à
  une session réelle sans **go distinct** de l'opérateur. Ne pas
  épuiser volontairement un quota pour fabriquer un scénario.
- Pas de fallback payant ou changement de fournisseur/compte implicite.
  Si une commande ou un chemin ne correspond pas à la version du dépôt,
  **s'arrêter et marquer non vérifié**, sans deviner.
- Les PID, chemins, UUID et éléments d'authentification sont des données
  potentiellement sensibles : publier seulement des comptes et
  identifiants expurgés. Les reçus bruts restent dans l'espace privé
  de l'opérateur, pas dans Git ou les commentaires GitHub.

## A. Baseline et branche Git (sans toucher à la production)

Utiliser **son propre** clone `<workspace>/source/` ; ne
jamais modifier la branche d'un autre agent. Depuis ce clone :

```sh
git status --short
git fetch origin
git rev-parse HEAD
git rev-parse origin/dev
```

Noter HEAD, date/heure, macOS, versions des applications concernées
et commandes réellement exécutées. Vérifier `AGENTS.md` et
`USAGE.md`. Prendre une issue **non déjà réservée**
(label `agent:<nom>`, marqueur `agent-claim`, branche
`codex/<numero>-<sujet>` avec `gh issue develop`) avant
d'écrire. Une réservation récente d'un autre agent est bloquante.

Pour lire/tester une branche qui ne vous appartient pas, utiliser un
worktree temporaire du **clone personnel** sous
`<workspace>/tmp/<sujet>/`, **sans commit dans cette branche**.
Le supprimer après revue. Les branches #161, #170 et #172 sont
des travaux distincts : vérifier le HEAD distant, ne pas supposer
leur fusion sur `dev`.

## B. Vérification passive : ne produit aucun tour modèle

1. Confirmer quel Desktop est réellement observé : application native,
   installation/instance, processus, mode de transport (stdio/socket/CDP)
   **seulement si observable**. Un PID ou un socket accessible ne prouve
   ni le propriétaire d'un thread ni la route de l'application.
2. Pour la branche #170, le scanner passif peut être lancé sur une racine
   **réellement constatée**, sans divulguer son JSON brut :

```sh
python3 -B reprise/codex_session_owners.py \
  --codex-home "$HOME/.codex" --max-threads 100
```

Documenter uniquement `status`, `session_count`,
`truncated`, `owner_state` et les profils
**anonymisés**. `truncated=true` avec `status=partial`
ne signifie pas une base corrompue : le plafond est atteint. Aucun
`owner_state=unknown` ne doit être transformé en propriété
prouvée.
3. Lorsqu'un message de quota apparaît **naturellement**, noter sa
   source visible (Codex, ReClaude, ChatGPT, Harness), le chat natif de
   test si autorisé, la date/fuseau annoncés et le type de refus.
   Ne pas confondre un message dans ReClaude avec le quota courant
   d'une fenêtre ChatGPT Desktop, même si l'UI Reprise les présente côte
   à côte.
4. Contrôler la concordance entre événement enregistré et interface
   réelle. Si la source est différente, rapporter `SOURCE_MISMATCH`.
   Si l'événement est lisible mais le quota actuel est inconnu, rapporter
   **`QUOTA_EVENT_OBSERVED_CURRENT_UNKNOWN`**, pas « aucune
   interruption ». Si l'événement n'est pas attribuable au bon chat,
   conserver l'observation et ne rien envoyer.

## C. Tests automatisés sans impact de production

Dans le worktree de la branche testée, utiliser les commandes
présentes dans le dépôt :

```sh
python3 -m unittest discover -s reprise/tests -q
git diff --check
git status --short
```

Si les dépendances du Mac manquent, ne pas remplacer les scripts
par des assertions inventées ; consigner le code de retour et
le diagnostic. Les tests de fixtures prouvent les algorithmes,
**pas** la reprise réelle d'une session Desktop.

La branche #172 retire **à titre conservateur** le mode socket
Desktop→`codexd` : sur cette branche seulement,
`shared_mode.prepare|launch|check|stop` doivent refuser
avec `status=unsupported` **sans lancer ni arrêter
aucun processus**. Vérification ciblée :

```sh
python3 -m unittest discover -s reprise/tests \
  -p 'test_shared_mode.py' -q
```

Ne jamais annoncer que ce comportement existe sur `dev`
avant fusion effective. Les autres moteurs Codex explicitement
configurés restent indépendants du mode partagé Desktop.

## D. Essai natif de continuation : uniquement avec un go distinct

Cette section est un **protocole de validation future**, pas
l'autorisation de lancer une inférence.

1. Choisir avec l'opérateur une conversation **jetable** dans le bon
   Desktop ; identifier le même chat avant et après. Ne pas utiliser
   une session de production ayant des actions outillées en cours.
2. Utiliser uniquement un **refus de quota naturellement observé** ou
   une fixture synthétique pour les tests automatiques. Capturer
   événement, provenance, échéance et identité natives.
3. À l'échéance annoncée, confirmer que le service Reprise et
   l'adaptateur natif ciblent cette conversation. Une lecture de
   compteur **ne doit pas être imposée avant la première tentative**.
4. Après autorisation expresse, permettre une seule continuation de
   test dans **le même chat**. Vérifier indépendamment : intention
   persistée, réservation, accusé du canal, tour natif observé,
   puis résultat. Un reçu d'admission n'est pas un résultat.
5. En cas de timeout ou retour incertain : inspecter les événements
   du chat avant un nouvel essai. Un second prompt peut déclencher
   des outils ou des effets externes : ne pas supposer qu'il sera
   toujours anodin.
6. Sans propriétaire/canal démontré, conclure
   `WAITING_ROUTE` en conservant l'événement, **pas**
   `RESUME_SUCCEEDED`.

Ne pas lancer une expérience destructive de verrouillage ou de
transfert entre deux app-servers Codex. Un `thread/read`
ne prouve pas que le writer appartient à `codexd`.

## E. Rapport, commit et passation

| Champ | Obligatoire |
| --- | --- |
| Baseline / branche / HEAD | Trois valeurs exactes, statut Git propre avant modification |
| Périmètre | Issue, fichiers autorisés, propriétaire du claim |
| Source observée | Harness + instance native, compte/pool seulement si prouvés |
| Événement | Type, portée, échéance ou « inconnue », provenance et fraîcheur |
| Action | Observation seule / tentative autorisée / jamais tentée |
| Preuves | Codes retour, sorties **expurgées**, tests ciblés et suite complète |
| Verdict | `PASS` / `FAIL` / `NOT_VERIFIED` / `SOURCE_MISMATCH` / `WAITING_ROUTE` |
| Risques et suite | Non-régressions, étapes manquantes, responsable du prochain lot |

Si un patch **était explicitement demandé**, ne modifier que les
fichiers autorisés et **ne committer qu'après vérification** :

```sh
git diff --check
python3 -m unittest discover -s reprise/tests -q
git status --short
git diff --stat
```

Puis, **sur la branche d'issue revendiquée et uniquement celle-ci**,
créer un commit explicite :

```sh
git add -- <fichiers-autorises>
git diff --cached --check
git diff --cached --stat
git commit -m "docs(#NUM): <objet-verifie>"
git rev-parse HEAD
git push origin HEAD
```

Ne pas exécuter les lignes d'exemple sans remplacer les marqueurs ;
ne pas utiliser `git add -A` dans un workspace partagé.
Attendre les quatre shards CI et le build lorsqu'ils s'appliquent,
consigner exactement leurs statuts, **ne pas annoncer « vert » pendant
`queued` / `in_progress`. Poster un checkpoint sur
l'issue : `<!-- agent-checkpoint: ... -->` + SHA,
résultats et limites. **Pas de merge automatique vers `dev`
ou `main`** ; cette décision reste opérateur.

### Demande à adresser à l'agent local

> Observe et teste uniquement l'issue et la branche fournies. Ne
> redémarre aucun daemon, ne modifie aucune conversation et n'infère
> pas la propriété d'une session d'après le socket ou la base SQLite.
> Identifie la source réelle du refus de quota, distingue événement
> reconnu et disponibilité actuelle inconnue, puis retourne des
> preuves expurgées. Si un correctif est explicitement autorisé,
> teste-le, commite-le sur ta seule branche d'issue et transmets le
> SHA et la CI. Ne fusionne rien.
