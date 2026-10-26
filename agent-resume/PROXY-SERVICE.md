# Proxy ASI1 Python géré par Reprise

Le proxy est optionnel pour l'orchestration DSH/Reprise ; il vise le planner
serveur, distinct de la délégation par outils. Exigences, identité par job,
paramètres mini et limites de preuve CUDOS :
[contrat ASI1](ASI1-ORCHESTRATION-CONTRACT.md).

launchd exécute le démon. Reprise gère son installation, sa configuration,
son démarrage, son arrêt et sa mise à jour. DeepSeek Harness reste client :
aucune autorisation d'exécution en arrière-plan ne lui est ajoutée.

Dans les intégrations Reprise, section « Proxy ASI1 », préparer l'installation,
examiner le plan puis l'appliquer. Le proxy utilise exclusivement le Python
embarqué de Reprise et sa bibliothèque standard. Aucun runtime Node, paquet
pip, téléchargement de dépendance ou credential n'est copié depuis un harness.

Les helpers et le runtime vérifiés sont installés dans une release immuable :
`~/Library/Application Support/Reprise/integration-releases/<sha256>/`.
Le service reste utilisable après suppression du clone de compilation.
Le plist et son manifeste d'ownership sont gérés par Reprise. L'inspection
compare les arguments et l'environnement effectivement chargés par launchd.

L'activation après installation est explicite. Sans activation, le service reste
arrêté, y compris au prochain login. Les actions démarrer/arrêter sont disponibles
dans Reprise. Démarrer un service déjà chargé ne le redémarre pas.

Le label existant `sas.heraisys.asi1-planner-proxy` est conservé : aucun deuxième
proxy n'est lancé en parallèle. Pour migrer le service Node de #74, cocher
l'adoption explicite. Elle exige un plist et des arguments chargés concordants,
l'empreinte exacte du script historique #72, et uniquement HOME dans
l'environnement déclaré de l'ancien plist. Les nouveaux arguments utilisent
`python3 -E -s -B .../asi1_planner_proxy.py` dans la release Reprise.
Un service personnalisé ou inconnu demande une réconciliation.

Avant une interruption, le PID natif doit être connu et lsof ne doit montrer
aucune connexion TCP établie. Ne pas activer de nouveaux clients pendant une
migration. Un port occupé par un autre lanceur interdit toute activation.
Une opération launchd incertaine, un timeout ou un changement concurrent conserve
un reçu `pending` et interdit le rejeu automatique. Inspecter alors le plist,
`launchctl print`, les empreintes du plan et les logs ; documenter l'état réel
avant de réconcilier. Ne pas éditer le manifeste pour contourner la protection.
Le dernier reçu conserve l'ancien plist pour examen.

CLI équivalente, depuis les helpers embarqués avec le Python embarqué :

```sh
python3 -E -s -B resume proxy-service-inspect
python3 -E -s -B resume proxy-service-plan --adopt --activate
# Passer le JSON {"plan": ...} obtenu en entrée standard :
python3 -E -s -B resume proxy-service-apply
python3 -E -s -B resume proxy-service-plan --action stop
python3 -E -s -B resume proxy-service-plan --action start
```

Après application, vérifier les arguments effectivement chargés, le PID du
listener 127.0.0.1:8481 et un GET sans authentification attendu404.
Un 404 ne prouve aucune inférence ni l'utilisation du preset #73.
Le retour après logout/login et le test DSH e2e restent des validations distinctes.
La gestion du service ne modifie ni profils, ni registres de tâches, ni sessions.

## Décision de langage — 25 octobre 2026

Décision utilisateur acceptée (#78) : Python pour cohérence avec les helpers et
le runtime déjà embarqués. Node était un choix du contrat initial #72 (« stdlib,
zéro dépendance npm »), sans nécessité fonctionnelle liée à ASI1. Rust aurait
ajouté une chaîne de compilation alors que ce service n'a pas de besoin de
performance mesuré. Conséquence : les tests doivent couvrir explicitement les
framing HTTP, streaming binaire, délais et annulations assurés par la stdlib.

## Limites et audit

Voir [la revue de sécurité](security-audit-78/REVIEW.md). Le flux est relayé sans
attendre sa réponse complète. Les logs de trafic sont bornés à5MiB avec3archives ;
les alertes stderr sont limitées aux changements d'état de journalisation.
La résolution DNS système ne peut être annulée : elle conserve un des32permis
jusqu'à son retour. Le client expiré est fermé et aucun POST tardif n'est envoyé.
Une panne DNS peut donc rendre le proxy temporairement indisponible, sans
croissance illimitée de threads. Le test de logout/login reste à faire au prochain
cycle utilisateur réel ; aucun logout artificiel n'est déclenché.
