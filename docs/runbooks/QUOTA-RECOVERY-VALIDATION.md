# Vérification terrain de la reprise après quota — agents locaux

**Référence de procédure**, non compte-rendu d'essai. Application et
composants : Reprise / Codex / ChatGPT Desktop / ReClaude / Harness.
Issues : #10, #14, #132, #161, #170, #172 et #174.

## Mission et autonomie

Les agents intervenants sont **techniquement compétents**, mais ne
doivent pas arbitrer seuls une hypothèse architecturale à partir d'un
résultat ambigu. Ils peuvent choisir leurs outils et leurs fixtures,
réaliser une investigation, exécuter les tests et préparer un commit.
Ils **ne doivent pas** modifier un service opérateur ou une session réelle
sans autorisation explicite et ciblée. Le coordinateur ou l'expert tranche
les divergences métier et l'attribution de propriété inconnue.

Chaque essai doit annoncer *quelle hypothèse il cherche à infirmer*,
sa baseline SHA, les fichiers autorisés, les préconditions et un résultat
classé **confirmé / réfuté / non vérifié**. Éviter les rapports génériques
« test OK » sans preuve observable.

## Hypothèses à départager

| Hypothèse | Preuve requise | Faux positif à éviter |
| --- | --- | --- |
| Refus de quota réel | Type/erreur native, source, heure et session attribuée | Confondre `current_quota_status=unknown` avec absence d'événement |
| Bonne instance Desktop | Bundle/processus/profil/session corrélés à l'événement | Lire le bandeau d'un autre Desktop |
| Échéance exploitable | Reset natif daté/fuseau fiable ou essai borné sans échéance | Promettre que quota est déjà disponible |
| Thread Codex accessible | Propriétaire réellement relié au client et identité de thread | `state_5.sqlite`, PID et `thread/read` comme preuve d'écriture |
| Dispatch effectif | Reçu natif du bon transport et de la même session | Une admission MCP ou un socket vivant |
| Travail achevé | Tour et effets métier réconciliés | « message envoyé » ou « fin de tour » comme succès final |

Sur une même observation, séparer **la cause passée** et **la
disponibilité actuelle**. Un état de quota indéterminé n'est pas une
raison de supprimer la demande de reprise ; une session destinataire
indéterminée peut en revanche empêcher un envoi sûr.

## Campagne en cinq lots indépendants

1. **Relecture seule (sans toucher aux processus).** Prendre un clone
   propre de la branche testée ; collecter la version Mac/binaire et
   les états autorisés. Lire les journaux de quota et les index locaux
   sur un échantillon borné, expurger contenu des chats et comptes.
   Rechercher l'événement de quota exact et son Desktop d'origine.
2. **Fixtures déterministes.** Tester quota confirmé + compteur courant
   inconnu ; quota historique + mauvais profil ; échéance annoncée ;
   historique ancien au-delà de 24 h ; index `LIMIT 100` tronqué ;
   doublon d'événement ; envoi ambigu. Aucun service ni inférence réelle.
3. **Test de routage passif.** Lorsque l'opérateur autorise l'observation
   native, mesurer l'attachement réel de Desktop à son app-server, à
   plusieurs instants. Ne pas conclure depuis `thread/read` seul.
   Les droits d'écriture et le rattachement stdio ne se déduisent pas
   de la parenté PID ou du socket `codexd`.
4. **Essai d'envoi réel uniquement sur autorisation ciblée.** Travail
   et chat de test identifiés, absence d'opération externe non réversible,
   même identité native, permission et quota de destination confirmés
   selon l'adaptateur. Une seule continuation courte à l'échéance,
   puis observation du tour réellement exécuté. Ne pas toucher à la
   session privée de l'opérateur sans accord.
5. **Réconciliation et commit.** Prouver absence d'envoi au mauvais
   profil, conserver les échecs et l'état incertain sans boucle rapide ;
   associer événement et reçu ; joindre les logs expurgés en commentaire
   d'issue, **jamais** dans l'arbre Git.

## Garde-fous et conditions d'arrêt

- Ne jamais redémarrer/arrêter `codexd`, fermer Desktop, effacer les
  locks ou modifier le bundle signé pour « réparer » une expérience.
- Ne jamais déduire le droit d'écriture d'un `thread/read` réussi,
  d'une connexion socket ou d'une UUID dans `state_5.sqlite`.
- Ne pas utiliser l'API Accessibilité macOS.
- Pas de bascule implicite vers un compte ou fournisseur payant,
  pas de copie de cookies/tokens, pas d'inférence non autorisée.
- Une incertitude de résultat de **dispatch** doit être réconciliée
  avant tout second essai : un doublon de demande conversationnelle
  peut provoquer des actions d'agent non idempotentes.
- Un échec de la route native = attente avec motif, pas travail perdu.
- Deux tentatives sans progrès ou contradiction d'identité =
  arrêter l'essai et rendre les preuves au coordinateur.

## Commit et transfert entre agents

1. Lire `AGENTS.md`, `USAGE.md` et le dernier `agent-claim` de
   l'issue. Partir d'un SHA fourni ou vérifier la nouvelle baseline.
   Respecter `<workspace>/source/`, worktrees temporaires
   `<workspace>/tmp/<sujet>/`, et la coordination via `origin`.
2. Pour un correctif autorisé, créer la branche liée
   `codex/<numéro>-<sujet>`, label unique `agent:<nom>`,
   commentaire `<!-- agent-claim: ... -->`. Pas de travail dans la
   branche détenue par un autre agent et pas de merge automatique.
3. Modifier uniquement les fichiers autorisés. Tests ciblés, puis
   `python3 -m unittest discover -s reprise/tests -q` ;
   `swiftc -parse` si Swift modifié, `sh -n` si shell,
   vérification packaging si documentation embarquée modifiée.
4. Publier SHA de baseline, SHA de commit, diff intégral vérifiable,
   fichiers modifiés, tests effectivement exécutés et statut CI.
   Si une CI n'est pas terminée : **non vérifiée**, jamais « verte ».
5. Laisser à l'opérateur le choix de fusion/déploiement. Le rapport
   final inclut un verdict, les preuves, les limites, les risques
   restants et les tests locaux à répéter après mise à jour.

**Critère d'acceptation produit :** signal de quota attribué → intention
persistée → essai natif à l'échéance quand la route est sûre →
résultat réellement observé. Cette chaîne n'est **pas encore
uniformément opérationnelle** sur tous les harness.
