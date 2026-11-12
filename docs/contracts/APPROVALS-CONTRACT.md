# Approbations Codex — contrat de sécurité (#117)

## Constat de topologie

Les requêtes `item/commandExecution/requestApproval` du serveur Codex sont
adressées à la connexion propriétaire du thread. Le pont Reprise persiste la
demande dans `desktop_approvals`, mais l'interface Codex Desktop peut utiliser
un autre app-server et ne reçoit donc pas nécessairement cette requête. Le
`codex app-server proxy` ne transfère pas à lui seul la propriété des requêtes.

## État livré

- **T1** : `approval_list` expose les demandes en attente, sans réponse.
- **T2a** : `approval_respond` MCP retourne `approval_disabled` avant tout
  rattachement au thread. Le modèle ne peut pas répondre aux approbations par
  ce canal. La demande reste `pending`.
- **T2b, incrément initial** : `approval_delegation.py` permet seulement à
  l'opérateur de créer, lister et révoquer des **préautorisations inactives**
  dans `quick.sqlite`. Ce n'est **pas** une livraison d'approbations.

Une préautorisation fixe un projet canonique, un harness déclaré, un workspace
canonique, **un thread UUID unique**, une durée parmi 1 h / 24 h / 7 j
(défaut 24 h), et un plafond `max_decisions` entre 1 et 100 (défaut 1).
Le jeton aléatoire à forte entropie est enregistré dans un fichier créé
exclusivement en mode 0600, à l'extérieur du dépôt et dans un dossier privé.
Seule son empreinte SHA-256 est conservée dans SQLite. Le listing n'expose
jamais de secret. La révocation est persistée et idempotente.

**Aucune consommation de jeton, vérification de l'identité du délégataire,
réponse JSON-RPC, ni approbation native n'est implémentée dans cet incrément.**
La présence d'un grant ne constitue jamais un consentement humain à une
commande déterminée. Le champ `harness` est une identité déclarative,
pas une preuve cryptographique ou une identité validée contre le manifeste.
Un processus malveillant du même utilisateur peut lire des ressources
accessibles à cet utilisateur : un fichier 0600 ne prouve pas la volonté
humaine. Ne pas exposer de jeton dans un modèle, un reçu ou un log.

## CLI locale d'administration

Les commandes suivantes exigent des dossiers existants et des chemins absolus
pour le projet, l'état et le workspace. Elles sont destinées à une session
opérateur hors des outils MCP du modèle ; jamais à une automatisation des
approbations.

```sh
reprise/reprise approval-delegation-grant \
  --project /chemin/projet --state /chemin/etat-prive \
  --harness harness --workspace /chemin/workspace/source \
  --thread-id 12345678-1234-1234-1234-123456789abc \
  --duration 24h --max-decisions 1 \
  --token-file /chemin/jetons-prives/grant-117.token

reprise/reprise approval-delegation-list \
  --project /chemin/projet --state /chemin/etat-prive

reprise/reprise approval-delegation-revoke \
  --project /chemin/projet --state /chemin/etat-prive \
  --grant-id UUID
```

Le dossier des jetons doit être privé (mode 0700), hors du dépôt Git.
Le fichier de jeton doit être absent avant création et ne doit pas être
committé. Les chemins ci-dessus sont des paramètres illustratifs et
ne désignent aucune installation existante.

## Conditions avant activation future

1. Valider l'identité stable du harness et du workspace contre un manifeste
   vérifié, indépendamment des valeurs fournies par l'appelant.
2. Vérifier atomiquement : portée projet/thread, approbation encore pending,
   durée, révocation, plafond restant, catégorie d'action autorisée et jeton
   **à usage unique lié à l'approval_id**.
3. Réserver atomiquement `BEGIN IMMEDIATE`, émettre uniquement à la
   connexion Codex prouvée propriétaire du thread, et ne marquer l'approbation
   résolue qu'avec preuve native ; en absence de preuve, garder une incertitude
   explicite sans seconde réponse automatique.
4. Ne jamais utiliser l'outil MCP `approval_respond` actuel. Le chemin
   opérateur doit être hors de la capacité du modèle ; l'origine CLI seule
   n'atteste pas la présence humaine si le modèle peut invoquer ce CLI.
5. Sur grant manquant, expiré, épuisé, révoqué, catégorie refusée ou jeton
   incorrect : **retour à l'humain**, demande conservée en `pending`, sans
   auto-approbation ni décision implicite.
6. Après preuve, afficher les demandes pending, décisions et identités dans
   une interface SwiftUI (T2c) ; aucun résultat de délégation n'est déduit
   du seul envoi JSON-RPC.

## Vérifications et frontières de preuve

Tests statiques et unitaires à exécuter par le harness repreneur :
`python3 -m unittest reprise.tests.test_approval_delegation
reprise.tests.test_approval_surface -v`, puis
`python3 reprise/ci_test.py --parallel 8 --force` (nouveau run impératif
sur la branche, ne pas réutiliser un log d'un autre SHA). Vérifier aussi le bundle autonome et
la conservation de l'accès en lecture seule à `approval_list`.
Tests ciblés, CI et protocole Codex réel **non exécutés par ChatGPT** dans
le lot initial. Il n'existe encore aucune preuve de livraison E2E.

Références : issue #117 (observations et arbitrages du 08–12 novembre 2026),
`docs/contracts/CODEX-EXECUTION-CONTRACT.md`,
`reprise/codex_expert_mcp.py`, `reprise/desktop_task_store.py`.
