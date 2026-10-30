# Lot #15 — collecte et reset Claude

Baseline : `a1009189e9e85114ead0f4818b58a662f9ab6dd9`. Réservation Codex et branche liée `codex/15-claude-collecte-reset` sur l'issue #15 ; le lot CDP actif #36 et ses mécanismes sont conservés.

Le plan a été soumis à Claude Desktop puis récupéré par `submit_result` via Reprise ; l'avis portait sur une baseline antérieure. Décisions retenues : pagination bornée par projet, reçu de livraison incertaine sans rejeu, consultation puis acquittement explicite, source de quota distincte et attribution inconnue, aucun pourcentage inventé. Les chargements locaux et par Claude sont des preuves différentes.

La conception et l'implémentation ont été déléguées par lots isolés ; une revue indépendante a conduit à corriger notamment la coexistence avec des anciens writers, les paramètres JSON-RPC malformés, la boucle de pagination vide et l'affichage après retrait du connecteur. Les ports ont été réconciliés avec les changements CDP de #36, sans modifier le workspace de DSH.

L'échéance du journal est une donnée d'événement, pas la preuve d'un état courant ; l'interface affiche sa provenance, l'heure de lecture et l'incertitude de fraîcheur. La lecture du journal ne déclenche ni reprise, ni inférence, ni modification du registre de quotas. Les autres indicateurs restent inchangés.

Contrôles sur copie isolée : suite déterministe avec Node 24 et l'archive Python épinglée, **1 117 tests exécutés, 2 ignorés, aucun échec** ; tests ciblés de collecte, de sockets et de transport CDP réussis. Compilation Swift complète réussie après extraction de sous-vues ; la revue indépendante a aussi fait corriger la présentation d'une tentative de lecture lorsque le journal est indisponible. L'observation locale du journal a révélé une échéance passée avec fraîcheur inconnue ; aucun état courant du quota n'en a été déduit. Le chargement dans Claude et le parcours graphique réel ne sont pas établis par ces tests.
