# Proxy Python — périmètre de sécurité #78

Date : 25 octobre 2026. Décision opérateur : Python stdlib, launchd exécute,
Reprise installe et gère. DeepSeek Harness est un client.

Les entrées non fiables sont les requêtes HTTP locales, les réponses du fournisseur
et les déconnexions/retards. Le démon n'a pas de credential propre ; il transmet
l'Authorization fournie par le client à l'origine ASI configurée explicitement
par le gestionnaire. Aucune découverte de clé, aucun accès au registre de tâches,
aucun lancement de commande demandé par le réseau, aucun retry d'inférence.

Le listener est limité à loopback. Le marqueur est un filtre de routage, pas une
authentification entre processus du même compte macOS. Un processus ayant déjà
les droits de cet utilisateur n'est pas isolé par ce marqueur. Aucune clé passée
par un autre client ne lui est révélée. Une isolation forte entre processus du
même UID nécessiterait un mécanisme d'autorisation distinct.

Invariants à vérifier : framing HTTP non ambigu et borné, relais SSE binaire avec
backpressure, coupure upstream au départ du client, délais séparés, concurrence
bornée, TLS avec validation certificat/hostname, journaux sans contenu ni secrets,
release/runtime vérifiés et indépendants des clones, configuration chargée vérifiée,
adoption connue explicite, aucune interruption de flux actif, pending sans rejeu
lors d'une opération launchd incertaine.

La résolution DNS native peut dépasser le délai connect : vérifier que les
ressources restent bornées et qu'une requête expirée ne peut plus envoyer de
corps/credentials après une résolution tardive. Examiner aussi la croissance des
logs et les ouvertures de fichiers symboliques. Les délais des tests sont réduits
sur fixtures ; cela ne remplace pas un test TLS de bout en bout.

Revue indépendante demandée via le pont Reprise installé : backend codex,
modèle gpt-6-astra, niveau deep, mode expertise lecture seule, aucun repli.
Clé persistée avant appel dans request.json. Pont temporairement lié à la copie
isolée #78 pour cette invocation uniquement ; config Codex/DSH inchangée.
L'admission et la présence d'un reçu ne constituent pas un résultat de revue.
