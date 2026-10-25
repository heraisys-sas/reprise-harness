# ASTRA-QUICK-APPLY — contre-analyse et expérience discriminante (préliminaire)

**Statut : préliminaire.** Deux blocages précis empêchent de conclure (voir §4) : le bundle `app.asar` de Claude Desktop n'est pas accessible depuis l'environnement qui a produit ce document, et `MISSION.md`, `FOLLOWUP-EVIDENCE.md`, `ANALYSIS.md` (et les éventuels `QUICK-APPLY-NEXT.md` / `COWORK-RPC-MAP.md`) ne figurent dans aucune branche du dépôt. Rien ci-dessous n'est issu d'une lecture du bundle courant.

Base du dépôt au moment de la rédaction : `dev` `e159fe2` (25/10), `VERSION` 0.1.13. Les versions actives (Reprise installée, Claude Desktop, Codex) évoluent : aucune conclusion de ce document ne vaut au-delà de la version dont elle est tirée, et le recensement du §5 est à relancer à chaque changement de version de l'application.

Niveaux de preuve : **[DÉPÔT]** lu dans le dépôt, **[RAPPORTÉ]** affirmé dans #36 sans accès à la pièce brute, **[HYPOTHÈSE]** à confirmer.

Contraintes respectées : aucune automatisation d'interface (AX, interface web), aucun contournement d'authentification, d'attestation, de fuses ou d'intégrité, aucune modification ni injection dans Claude, aucun secret, cookie ou historique privé, aucune mutation de service, de VM, de redémarrage ou de permission, aucun repli par API payante.

## 1. Ce qui est établi, par étape

| Étape | État | Niveau |
|---|---|---|
| **Préparation** (valider et admettre un payload) | `claude_desktop_quick.py` valide, borne et enregistre ; ne soumet rien (« intentionally left to the supervised driver »). Reçus `admission_only: true` | [DÉPÔT] |
| **Livraison** (faire émettre un tour par Claude) | Un seul point d'entrée non visuel connu : `dispatchOnQuickEntrySubmit`, atteint par `FCr → V4r → K4r/G4r` (bundle 2.42304.2). Un essai réel du 19/10 a prouvé le tour **avec ouverture manuelle de Quick Entry** ; le mode non surveillé n'est pas prouvé | [DÉPÔT] `CLAUDE-QUICK-ENTRY-INTERNALS.md` |
| **Livraison, aucun endpoint externe** | `quick_entry/submit` n'est pas branché au dispatcher ; remote-debugging refusé, fuses `RunAsNode`/`NODE_OPTIONS`/inspect désactivés ; pas de dictionnaire AppleScript ni d'App Intent adapté ; LocalPairing et « Change with Claude » ne ciblent pas un `chatId` | [RAPPORTÉ] #36 (23/10) |
| **Retour MCP** | `get_task` / `submit_result` : seul `submit_result` vaut retour. `tools/list` et l'admission ne prouvent ni invocation ni livraison. Le serveur MCP local ne déclenche pas un tour | [DÉPÔT] + [RAPPORTÉ] #36 (24/10) |
| **Garde de compte** | `U4r` compare l'identité à la soumission ; un payload externe passerait par la même garde (même compte : pas un obstacle) | [DÉPÔT] |

## 2. Contre-analyse

**C1 — Deux numéros de version.** La chaîne `FCr/V4r/K4r/G4r` a été extraite du bundle **2.42304.2** ; l'inspection d'Astra du 23/10 porte sur le « package **3.1623.4** ». Si ce sont deux versions du produit, les identifiants minifiés et peut-être la chaîne ne s'appliquent plus ; si c'est un autre schéma de numérotation (version d'application et build), il faut le dire. Toute conclusion négative repose aujourd'hui sur la seconde, toute description de chaîne sur la première. *Argument technique : une négation établie sur un bundle ne vaut pas pour un autre.*

**C2 — Entrées natives non mentionnées dans le texte accessible.** La liste des pistes écartées couvre le débogage, les fuses, LocalPairing, « Change with Claude », AppleScript et App Intents. Elle ne mentionne pas :
- les **appelants du chemin de repli `z4r(e)`** (`yCr() || V4r({text: e ?? "", images: []})`, document d'extraction §4), qui rejoint le routage sans overlay Swift ;
- un **schéma d'URL** (`CFBundleURLTypes` / événement `open-url` / `setAsDefaultProtocolClient`) ;
- un **service macOS** (`NSServices`), invocable par programme sans permission d'accessibilité ;
- une **activité utilisateur** (`NSUserActivity`/Handoff), `second-instance` (arguments) et `open-file`.

Ces mécanismes sont publics et courants dans les applications Electron ; l'absence de preuve dans le texte lu n'est pas une preuve d'absence, mais `ANALYSIS.md` les couvre peut-être : à vérifier avec les fichiers manquants. Limite connue d'avance : le chemin de repli construit `{text, images}` **sans `chatId`** (nouvelle conversation, pas la conversation cible).

**C3 — La piste `companion-web`.** Elle est exposée comme prototype sans AX mais repose sur une interaction « via l'interface web » d'un navigateur dédié. Cela ressemble à de l'automatisation d'interface, que la mission interdit, et ne rejoint pas `dispatchOnQuickEntrySubmit`. À trancher explicitement avant tout prototype.

**C4 — #36 se reformule si une entrée existe (décision de l'opérateur du 25/10).** Si une entrée atteint `dispatchOnQuickEntrySubmit(chatId, prompt)`, le bouton natif « Continuer » n'est plus nécessaire : un message texte de continuation, explicite et consigné, envoyé au même chat après l'échéance est désormais accepté en second mode (#36 corrigée). Reste **[HYPOTHÈSE]** à valider : la pause gratuite n'empêche pas un nouveau message après reset, et le bouton ne fait pas plus qu'envoyer un message. Le prompt n'est jamais silencieux et ne remplace jamais celui d'une tâche.

## 3. Meilleure piste positive

**Aucune piste n'est vérifiée à ce jour, et je n'ai pas d'appel exact à proposer.** Candidates, par ordre de plausibilité décroissante (toutes **[HYPOTHÈSE]**) :

| # | Mécanisme | Appel côté Reprise si confirmé | Limite probable |
|---|---|---|---|
| 1 | Appelants de `z4r` / chemin de repli atteignable par un événement natif | selon l'événement trouvé | pas de `chatId` |
| 2 | Route `open-url` aboutissant à `V4r` | `/usr/bin/open '<url exacte>'` (LaunchServices) | `chatId` à vérifier ; préremplissage seul possible |
| 3 | Service `NSServices` dont le message atteint `V4r` | `NSPerformService` depuis le composant Reprise | types de données du service |
| 4 | `NSUserActivity` / `second-instance` | selon le format | rarement `chatId` |

Écartées d'emblée : modifier `claude_desktop_config.json` (modification de Claude), envoyer des touches ou lancer l'overlay par clavier (automatisation d'interface), inspecteur ou injection.

## 4. Blocages précis

1. **Aucun accès au bundle.** L'environnement est un Linux sans `/Applications/Claude.app` ni `app.asar` ; je ne peux ni confirmer ni réfuter C1 et C2.
2. **Fichiers de mission absents.** `MISSION.md`, `FOLLOWUP-EVIDENCE.md`, `ANALYSIS.md`, `QUICK-APPLY-NEXT.md`, `COWORK-RPC-MAP.md` : aucun n'existe dans une branche de `heraisys-sas/reprise-harness`, ni dans mon espace disque. Je ne conclus pas sur un contexte insuffisant.

## 5. Plus petite expérience discriminante

**`python3 quickapply_census.py /Applications/Claude.app > census.md`** (outil livré avec ce document, lecture seule : ni lancement, ni réseau, ni écriture hors du rapport ; 7 tests ; lecteur `asar` validé contre une archive produite par l'outil officiel).

Il établit en une commande : (a) les schémas d'URL, services, activités, dictionnaires de script et App Intents **déclarés** par l'application ; (b) la **chaîne d'appels à rebours** depuis `dispatchOnQuickEntrySubmit` ; (c) les entrées externes (`open-url`, `second-instance`, `open-file`, `setAsDefaultProtocolClient`…) présentes dans le même script ; (d) si l'ancre existe encore dans le bundle installé (C1).

| Résultat | Conclusion | Étape suivante |
|---|---|---|
| Un événement natif de (c) mène à `V4r` / `G4r` | piste 1 à 4 confirmée statiquement | un seul essai de livraison avec un `request_key` dédié, après accord de l'opérateur ; admission ≠ émission ≠ retour `submit_result` |
| Aucune entrée externe n'atteint l'ancre | conclusion négative soutenue par preuve, pour cette version | arrêter la recherche d'entrée native ; trancher C3 ; relever Cowork |
| Ancre absente | la chaîne documentée ne correspond pas à ce bundle (C1) | relancer l'extraction sur la version installée |

Pourquoi c'est la plus petite : elle ne demande aucune permission macOS, ne touche à rien de vivant, et chaque résultat possible déclenche une action différente.

## 6. Quota et échéance (sans mesure préalable obligatoire)

Principe cohérent avec #64 et #59 : enregistrer l'échéance annoncée (avis d'opérateur, `awaiting_observation`, aucune empreinte inventée), tenter **une** fois à l'échéance connue sans lecture préalable obligatoire, puis diagnostiquer en lecture seule après un échec. Sans entrée native confirmée, aucune tentative ne part. La capture du texte natif de pause reste, aujourd'hui, dépendante d'Accessibilité (#36) : elle n'est pas remplacée ici.

## 7. Cowork

Non évalué : `COWORK-RPC-MAP.md` est absent. Le recensement s'applique aussi à une application Cowork s'il s'agit d'un bundle distinct (`python3 quickapply_census.py /chemin/vers/Cowork.app`). À juger après réception du plan RPC.

## 8. Questions à l'opérateur

1. Où se trouvent `MISSION.md`, `FOLLOWUP-EVIDENCE.md` et `ANALYSIS.md` (workspace Codex/Astra) ? Peux-tu les joindre ou les coller ?
2. Peux-tu exécuter `quickapply_census.py` sur le Mac et renvoyer `census.md` ?
3. « 2.42304.2 » et « 3.1623.4 » : deux versions ou deux numérotations ?
4. `open claude://…` (LaunchServices) et `NSPerformService` sont-ils acceptables au titre des interdits ? Ce sont des mécanismes publics, sans accessibilité ni contournement.
5. Une interaction via l'interface web (C3) relève-t-elle de l'automatisation d'interface interdite ?
