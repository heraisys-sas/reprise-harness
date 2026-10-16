# Pilote routage — plan exécutable hors ligne

Plafond utilisateur confirmé : **2 € au total**. Aucun appel modèle n'est lancé par ces scripts. Aucun routage quotidien, daemon, identifiant, modèle local supplémentaire ou réglage global n'est installé.

## Ordre retenu

1. Prévol gratuit : vérifier les oracles, les contrats de reprise/isolation et l'instrumentation. État actuel ci-dessous.
2. Vérifier accès/catalogue/facturation sur les routes déjà autorisées, sans lire ni transférer des jetons internes. Accès de compte actuellement inconnu.
3. Avant toute dépense, fournir un contrôle testé d'admission par requête : coût déjà engagé + borne d'entrée/sortie et retries, sous le plafond total. Le runner actuel ne fournit pas ce contrôle et ne peut pas lancer d'appel payant.
4. Premier lot : deux cas × A/B/C ; poursuivre les quatre suivants uniquement si les mesures couvrent aussi les enfants, échecs et corrections. Maximum conventionnel : 0,10 € par run complet, 18 runs = 1,80 € et 0,20 € de réserve. Les montants sont des plafonds proposés, pas des tarifs estimés.
5. Examiner la reprise/isolation, puis décider de poursuivre ou conserver Flash seul. Aucun résultat n'activera automatiquement le routeur quotidien.

Bras principaux : A `deepseek-official/deepseek-flash` low ; B même route high ; C Flash low avec lot ciblé sur `openai-codex/gpt-6-astra`, seulement si accès confirmé. Pro est un challenger distinct, sans remplacement silencieux. Sol/Luna et ASI restent secondaires pour ne pas diluer ce petit pilote. Voir [MODELS.md](MODELS.md) et [PROTOCOL.md](PROTOCOL.md) pour IDs, sources et limites.

```sh
python3 harness-tools/routing-eval/run.py
PATH="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/node/bin:$PATH" python3 harness-tools/routing-eval/run.py --check-fixtures
python3 -W error::ResourceWarning -m unittest discover -s harness-tools/tests
```

Le premier appel imprime le plan, sans exécuter de commande ni de modèle. Le second copie les starters synthétiques dans des dossiers temporaires privés et utilise l'agrégateur `verify.py` existant. Il doit constater leur échec aux assertions (exit 1). Un outil manquant/exit 2 reste indisponible, jamais un succès ; timeout et sortie bornés. Les commandes de vérification sont du code de projet de confiance, pas un sandbox contre un candidat malveillant. Les oracles publics sont hors de la copie de travail ; cela évite une modification accidentelle, pas leur lecture par un agent ayant accès au dépôt complet.

## Prévol exécuté le 16 octobre 2026

- 31 tests des outils passent (dont 2 tests du runner).
- Sept starters rejetés et sept corrections de référence temporaires acceptées : Python, JS avec Node embarqué, DevOps, reprise simulée et isolation.
- Rust : `rustc` absent des emplacements examinés et du PATH ; cas non validé, pas de téléchargement effectué. Le runner renvoie donc un statut global incomplet et exit 1.
- Huit cas sont publics et destinés au développement, pas un benchmark aveugle ni un heldout indépendant. Une seule migration Python→Rust est préparée ; JS→Rust reste une extension ultérieure.
- Accès modèles, coûts réels et qualité distante : inconnus ; aucun appel payant et aucune économie revendiquée. La comparaison distante est planifiée, pas exécutée.

Les fixtures ne contiennent que des données synthétiques. Les vraies sessions, sorties, credentials et dossiers de travail doivent rester privés hors Git. Arrêter d'invoquer le runner suffit au retour arrière ; aucune installation ni session quotidienne n'a été modifiée.
