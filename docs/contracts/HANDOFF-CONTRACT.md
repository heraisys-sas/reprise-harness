# Passation inter-harness

## Convention GitHub utilisable immédiatement

Une issue est le point de coordination ; sa branche est liée avec `gh issue develop`.
Réutiliser les labels `agent:<harness>` existants (notamment `agent:codex`,
`agent:claude`), sans créer une seconde famille `harness:*`.

- `work:active` : exactement un responsable `agent:*`, travail en cours.
- `work:available` : aucun responsable actif ; reprise proposée à un autre harness.
- `work:blocked` : intervention ou condition externe requise ; ce n’est pas une offre d’exécution immédiate.

Au début, lire le dernier checkpoint et les commentaires suivants, vérifier la
branche distante et annoncer la prise en charge. Ne pas remplacer le label d’un
responsable actif sans coordination. Réutiliser la branche liée si le lot continue ;
ne pas créer une nouvelle branche concurrente par défaut. L’auteur reste enregistré
dans les commits et commentaires, même lorsque son label de responsabilité est retiré.

Avant de rendre le travail disponible : publier les commits autorisés, vérifier le
SHA distant, puis poster un checkpoint et remplacer `work:active` et son `agent:*`
par `work:available`. Ne pas annoncer « poussé » sans vérification. Un travail non
publié ou une action distante incertaine doit être signalé explicitement.

Checkpoint minimal :

```text
HANDOFF v1
État : available | blocked
Issue : #...
Branche : ...
Commit distant vérifié : ...
Fait / preuves : ...
Non committé ou non publié : ...
Services, actions en vol et incertitudes : ...
Prochaine action bornée : ...
Vérification attendue : ...
```

Le repreneur remplace `work:available` par `work:active` et ajoute son `agent:*`,
puis relit l’issue. Si une autre prise en charge est apparue, se coordonner avant
modification. Ne pas poursuivre une inférence ni rejouer une opération incertaine
sur la seule présence d’un label.

## Automatisation Reprise à implémenter

Les labels sont des signaux humains, pas un verrou atomique. Pour une reprise
sans coordination humaine, Reprise devra être l’autorité des réservations :
réservation atomique par issue/lot avec owner, claim_id, génération, expiration
et renouvellement ; publication GitHub comme projection de cet état. Un claim
périmé ne prouve pas l’arrêt du harness : réconcilier les actions en vol avant de
réattribuer, et rejeter les écritures d’une ancienne génération. Conserver les
checkpoints, questions ouvertes, résultats non réclamés et liens de session.

L’installation des intégrations devra distribuer cette règle à chaque harness
compatible. Un arrêt volontaire avec passation se distingue d’une interruption
quota, d’une pause globale et d’une tâche terminée. Cette automatisation n’est
pas livrée par les labels ni par le présent contrat.
