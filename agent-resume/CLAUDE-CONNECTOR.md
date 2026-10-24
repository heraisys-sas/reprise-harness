# Connecteur Reprise Claude Desktop

## Installation depuis Reprise

Dans **Intégrations → Connecteur Claude Desktop**, choisir le projet autorisé,
prévisualiser l’installation puis appliquer le plan. Le bundle contient le serveur
et son runtime Python ; ni clone Git ni Python système ne sont requis chez
l’utilisateur. Un seul serveur MCP nommé `reprise` expose les deux sens.

L’option « Remplacer l’ancien probe Reprise reconnu » retire seulement l’entrée
`reprise_quick_probe` correspondant au script de test connu. La configuration
complète est sauvegardée en privé ; les autres connecteurs, préférences et secrets
ne sont pas copiés vers un autre harness. Une entrée `reprise` personnalisée ou un
changement concurrent bloque l’opération. Le retrait ne supprime pas les tâches
et résultats. Reprise ne redémarre pas Claude ; le chargement effectif après
rechargement est une preuve distincte de la vérification directe de la release.

CLI équivalente : `claude-integration-inspect`, `claude-integration-plan --project
CHEMIN --action install --replace-probe`, `claude-integration-apply` avec le plan
JSON en entrée standard, puis `claude-integration-verify`. Un plan de retrait
utilise `--action remove`.

## Même connecteur, deux sens

- `get_task(request_key, claim_key)` réserve une tâche entrante du projet. Persister
  les clés avant appel, puis réutiliser le même `claim_key` après interruption.
- `submit_result(request_key, attempt_id, answer)` enregistre le résultat final.
  Une seconde remise identique est idempotente, une réponse différente est refusée.
- `get_result(request_key)` renvoie un reçu compact. `view=page` permet la collecte
  avec empreinte et offsets ; `view=full` est explicite. L’acquittement est explicite.
- `reprise_backends`, `reprise_quotas`, `reprise_delegate`, `reprise_followup` et
  `reprise_requests` réutilisent les contrats, routes et protections existants.
  Préférer `return_mode=receipt` pour les réponses longues. Aucune inférence n’est
  lancée par l’installation ou la découverte des outils.

La délégation sortante vers `claude-desktop` est interdite depuis ce client pour
éviter une boucle immédiate. Les autres routes doivent être configurées et
respecter les contrôles de quota, de facturation et d’appropriation existants.
Aucune permission du système de fichiers ou d’exécution de Claude n’est étendue
par un avis reçu. Les clarifications automatiques durables restent le contrat
cible documenté, pas une capacité livrée par ce lot.

## Démon et reprise quota

Le démon `claude-desktopd` peut être lié au projet avec `--project-root`. Son
registre `quick.sqlite` est partagé avec le connecteur, isolé par projet. Une
soumission de délégation textuelle prépare une tâche ; les pièces jointes typées
non prises en charge sont refusées, jamais perdues silencieusement. Les anciens
probes et tâches non liés à un projet ne sont pas adoptés implicitement.

Le planificateur utilise le même `quick_entry/submit`, avec `prompt=retry`,
`chatId`, `purpose=continuation` et une clé stable. La continuation n’est pas une
nouvelle tâche d’expertise à réclamer via `get_task`. La route Quick Apply est
prioritaire ; Accessibilité demeure une option explicite séparée.

**Limite non résolue :** le pilote d’émission natif n’est pas raccordé au démon.
`quick_entry/capabilities` le signale : `dispatch_supported=false` et
`resume_validation_supported=false`. Le planificateur n’envoie pas aveuglément un
message dans ce cas et ne transforme pas une admission en preuve de reprise.
Le champ historique `dispatch` nomme la cible envisagée, pas un appel exécuté.
Le connecteur MCP répare le retour et la délégation, mais ne crée pas à lui seul
l’IPC manquante vers Quick Apply. Seul un résultat `submit_result` accepté clôt
une tâche entrante ; ni un socket vivant ni un reçu d’admission ne la clôturent.
