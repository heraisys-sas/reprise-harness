# Contrat de continuation après quota — tous les harness

**Statut : spécification cible, partiellement implémentée.** Décision opérateur du
14 novembre 2026 ; chantier transversal [#10](https://github.com/heraisys-sas/reprise-harness/issues/10).
Ce contrat précise le comportement métier, pas un engagement de capacités
natives que le logiciel actuel n'aurait pas démontrées. Il complète
[HARNESS-SUPERVISION-ARCHITECTURE](HARNESS-SUPERVISION-ARCHITECTURE.md),
[HARNESS-ROLES-CONTRACT](HARNESS-ROLES-CONTRACT.md) et
[QUOTA-KNOWLEDGE](QUOTA-KNOWLEDGE.md).

## Principe prioritaire

**Tout travail interrompu par un quota reconnu doit être conservé et sa
continuation native tentée dès que cela est admissible, sur le harness et la
session d'origine.** Le but n'est pas d'obtenir une certitude permanente sur
l'état courant des compteurs avant d'agir. Un message de quota suffisamment
attribuable est une preuve d'interruption ; la disponibilité **actuelle** du
quota peut rester `unknown` jusqu'à la tentative.

La détection, la planification et l'envoi sont **trois étapes distinctes**.
L'absence d'un propriétaire vérifié ou d'un adaptateur utilisable ne doit pas
effacer une intention de continuation. Elle limite seulement l'action. Un
échec de lecture de quota courant n'annule pas un événement déjà confirmé.

La préférence est une **tentative utile plutôt qu'un gel permanent** ; elle
ne lève toutefois ni l'autorisation de l'opérateur, ni la contrainte de
rester sur la bonne session, ni le principe « pas de facturation supplémentaire
implicite ».

## Périmètre des preuves — ne pas confondre

| Signal | Signification autorisée | Ce qu'il ne prouve pas |
| --- | --- | --- |
| Erreur native de type quota ou bannière de pause attribuée à un chat | Interruption par quota sur la source observée | Quota actuellement épuisé, identité d'un autre Desktop |
| Compteur ou journal `current_quota_status: unknown` | Disponibilité non mesurée ou non attribuable | Absence d'un blocage déjà observé |
| Échéance `retry_at` ou reset affiché et correctement daté | Première occasion prévue pour essayer | Réussite ou garantie de disponibilité |
| `~/.codex/state_5.sqlite` | Existence d'un thread dans cet index | Processus ou app-server propriétaire du writer |
| `thread/read` sur socket `codexd` | Thread lisible depuis cet endpoint | ChatGPT Desktop réellement connecté à ce socket |
| Processus Codex enfant de Desktop | Processus observé | Thread dont il possède les droits d'écriture |
| Reçu natif d'admission | Soumission acceptée à une étape du canal | Message remis, tour commencé, tâche métier terminée |

**Attribution de source avant toute action.** Conserver harness, identité native
du chat/thread, profil ou installation, instance et route observées,
horodatage/fuseau, type de preuve, fraîcheur, compte/pool lorsqu'établis.
Un bandeau du Desktop A ne renseigne pas le quota du Desktop B. Une erreur
d'attribution de bureau, compte ou canal doit devenir `source_mismatch`
ou `source_unknown`, jamais un état quota global fabriqué.
L'intention peut rester visible en attente, sans envoi à un mauvais destinataire.

## Machine d'états cible

1. **Observation** : classifier `quota_confirmed` lorsqu'un refus natif
   explicite et rattachable est observé ; `quota_candidate` si l'indice
   ne permet pas d'établir cette cause. Afficher les signaux de la bonne source.
2. **Enregistrement** : persister une intention unique par interruption native
   avec son `work_id`, sa session, sa provenance et l'échéance
   éventuelle. Une session dont le propriétaire RPC est inconnu reste
   enregistrée, pas écartée.
3. **Échéance** : programmer la première tentative à l'échéance annoncée.
   À l'heure annoncée, **ne pas imposer une nouvelle mesure de quota**.
   Si aucune échéance n'existe, choisir un contrôle/tentative borné
   compatible avec le canal ; cadence et limite explicites, sans boucle rapide.
   Après mise hors tension du Mac, une échéance dépassée reste due, sans être
   éliminée arbitrairement parce que l'événement date de plus de 24 heures.
4. **Pré-action légère** : respecter pause globale et autorisations ; identifier
   le **bon** harness, la **même** conversation, son état actuel et son
   propriétaire ou un canal de continuation natif autorisé ; refuser si
   l'utilisateur travaille déjà sur cette même session ou l'a clôturée.
   Ne pas substituer `codexd` à un app-server Desktop stdio privé par supposition.
   Si ces conditions ne peuvent être vérifiées, conserver
   `waiting_route` / `waiting_owner` et poursuivre la
   découverte passive. L'absence de compteur de quota actuel n'est pas un
   veto.
5. **Tentative** : réserver durablement *avant* l'envoi ; adresser une
   continuation native à la session d'origine, avec clé de requête stable
   si le transport en accepte une. Une nouvelle erreur de quota peut
   reprogrammer une échéance ; une acceptation ne vaut pas exécution réussie.
6. **Réconciliation** : observer l'accusé, l'ouverture/achèvement du tour et
   le résultat métier séparément. Un timeout **après** tentative peut
   signifier que le message a été livré ; marquer `dispatch_uncertain`
   et rechercher un reçu ou le tour natif avant tout renvoi.
7. **Sortie** : `work_validated` seulement sur preuve métier,
   pas sur une réponse « je travaille déjà » ni sur un simple code retour.

### Doublons : sûreté minimale, pas perfection bloquante

L'opérateur accepte qu'un doublon bénin soit moins grave qu'une absence
persistante de reprise. **Cela ne rend pas tout doublon inoffensif** : un
agent outillé pourrait répéter une écriture, un commit, une commande ou un
envoi externe. Cible pragmatique : réservation persistante par événement
et **une seule tentative incertaine à la fois**. Si une première tentative
est confirmée non reçue, un nouvel essai peut être autorisé ; si la livraison
est incertaine, examiner les événements natifs avant d'envoyer une seconde
consigne. Ne jamais bloquer l'ensemble des autres travaux pendant cette
réconciliation.

Aucun changement de modèle, fournisseur, compte, formule payante ou API
facturée sans décision explicite. Nouvelle session et checkpoint ne sont
possibles que lorsque le harness ne peut pas continuer dans la même
session et que la politique de transfert l'autorise ; signaler alors
qu'il s'agit d'une continuation du travail, **pas** du même thread natif.

## Codex Desktop, `codexd` et ChatGPT Desktop

Le relevé local rapporté en #170 observe **un seul index**
`~/.codex/state_5.sqlite` dans les emplacements examinés, avec
plusieurs processus Codex (Desktop et daemon). Il ne prouve pas un seul
propriétaire RPC, et le scanner est limité à 100 entrées et à une fenêtre
de fraîcheur. Sur cette installation, Desktop lancé **depuis Reprise** avec
une route socket demandée conserve ou retrouve son app-server stdio privé.
Le rattachement Desktop→`codexd` n'est donc pas établi par la
seule connexion socket de Reprise ou par `thread/read`.

Conséquences : `codexd` est préservé et n'est pas le daemon porteur
de ChatGPT Desktop ; `chatgptd` est le **nom cible** d'un porteur CDP
distinct (non déclaré livré sur `dev`). Les agents doivent garder les
canaux Codex app-server et ChatGPT conversation ordinaire distincts.
La branche #172 déprécie le mode socket Desktop partagé trompeur ;
la branche #161 expérimente le transport CDP pipe ; #169 conclut
négativement pour la voie codex-cli RPC vers les chats ordinaires de la
version étudiée. **Aucune de ces branches ne doit être présentée comme
fusionnée tant que `dev` ne l'atteste.**

## Écart entre cible et code actuel (baseline `dev@194ad1e9`)

- `scheduler.snapshot()` lit `detect.scan()`, donc
  principalement l'index `~/.codex`.
- `detect.scan()` ne lit que 100 threads non archivés et
  modifiés durant les 30 derniers jours, puis ne retourne que les
  interruptions présentant un état.
- `scheduler.confirmed()` exige actuellement une source de
  reprise admise, exclut certains sous-agents et les nouvelles interruptions
  de plus de 24 h : cela doit devenir une **restriction d'action**, pas
  une raison de ne pas conserver le travail.
- Claude Desktop possède des lecteurs de journaux de quota et une route
  native expérimentale ; le retour bout-en-bout sur le même chat est
  suivi par #132. Harness et Claude Code ne disposent pas encore tous
  d'un adaptateur de continuation native validé.
- Le contrôle « quota actuel inconnu » doit rester indépendant du signal
  « événement historique de quota reconnu », en particulier dans
  `claude_quota_log.source_observation()`.

La généralisation est **à livrer dans #10**, pas implicitement obtenue
par la mise à jour de ce document.

## Tests d'acceptation exigés

- Quota natif reconnu, compteur courant `unknown` : événement
  durable, tentative à l'échéance, aucune attente forcée de compteur.
- Message de quota visible dans un **autre** Desktop : aucune attribution
  croisée ; erreur de source lisible et événement préservé.
- Thread Codex stdio Desktop et index SQLite commun : observé sans
  prise de propriété par `codexd`.
- Travail devenu actif manuellement entre observation et tentative :
  aucun second message ; les autres travaux progressent.
- Mac arrêté plus de 24 h, ancien événement non traité : pas d'oubli
  par date ; tentative ou état d'attente explicitement motivé.
- Reçu perdu avant/après émission, deux ticks concurrents, scheduler
  redémarré : une seule intention durable, pas de réémission aveugle.
- Quota non levé après échéance : nouveau refus conservé, nouvelle date
  si annoncée, sans changement de route/facturation.
- Confirmation native du même chat et résultat terminé distincts de
  l'admission/dispatch.
- Aucune API Accessibilité macOS.

[Procédure de test et passation des agents locaux](../runbooks/QUOTA-LOCAL-ACCEPTANCE.md).
