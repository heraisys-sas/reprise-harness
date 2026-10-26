# Audit du proxy Python — #78

Revue indépendante Reprise : backend Codex, gpt-6-astra/high, même session
poursuivie après réconciliation native. Aucun fournisseur ni mode de facturation
substitué. Les données de transport et messages natifs restent locaux.

La première revue a été interrompue par le délai historique300s de #65 ; des
changements d'entrées avaient également invalidé sa collecte. Aucun rejeu ni
changement SQLite manuel. Après intégration/installation de #65, la continuation
s'est détachée à30s puis a terminé nativement en303s. Son résultat a été collecté
par reprise_reconcile, enregistré et acquitté. Cela prouve le parcours de
continuité du pont, pas la sécurité absolue du démon.

Défauts établis par Astra et corrigés avec régressions :

- Reconnexion implicite http.client après annulation : auto_open désactivé,
  connexion explicite unique, cancellation vérifiée, aucun POST tardif.
- Transfer-Encoding ambigu : validation alignée sur le décodeur, SSE exact.
- Expiration headers dans la fenêtre de détachement du socket : expiration
  irréversible et garde totale maintenue, absence de succès artificiel.
- Croissance stderr en cas de panne de logs : alertes uniquement aux transitions
  panne/récupération. Traffic JSONL limité à5MiB et3archives, fichiers réguliers,
  propriétaire vérifié,0600 et refus des liens symboliques.

Revue séparée du gestionnaire : arrêt113seulement, revalidation plist ET manifeste
après staging même service arrêté, démarrage attesté par arguments/environnement
chargés, PID running, listener du même PID et connexion TCP. Cas incertains
conservés en pending ; aucun rejeu automatique.

Contrôles ciblés :36tests réseau,19tests gestion. Les trois dernières régressions
ont été répétées10fois chacune. TLS avec vérification certificat/hostname et
contexte immuable réutilisé ; flux2MiB livré progressivement avant fin upstream.
Les tests n'appellent aucun fournisseur réel ni credential utilisateur.

Risque résiduel documenté : getaddrinfo système peut bloquer au-delà du délai.
Le client est coupé et chaque thread bloqué conserve son permis :32maximum,
pas de rotation créant des threads illimités. Une résolution tardive ne produit
aucun POST. Une panne DNS peut donc rendre ce proxy indisponible jusqu'au retour
du résolveur ; pas de garantie de délai strict sur l'appel système lui-même.
Le marqueur de routage n'isole pas les processus d'un même UID.
