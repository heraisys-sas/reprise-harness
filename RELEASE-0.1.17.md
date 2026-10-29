# Reprise 0.1.17 — attente et retour MCP Claude

La version commune `agent-resume/VERSION` passe à 0.1.17. L'installateur la propage aux champs du bundle, au manifeste des helpers et aux annonces des connecteurs ; les runtimes et plugins indépendants gardent leur propre version.

L'outil `await_task` permet d'attendre une tâche précise pendant une durée bornée. Il est masqué par défaut et s'active explicitement avec `--enable-await` dans l'intégration Claude gérée ; une mise à jour ordinaire conserve ce choix. La livraison et le retour utilisent MCP (`await_task` ou `get_task`, puis `submit_result`). Après une tentative de livraison, les deux outils rendent un reçu sans rejouer le prompt. Ce reçu ne prouve pas que Claude a reçu le prompt ; une situation incertaine doit être réconciliée, sans contourner la barrière avec une nouvelle clé.

La migration des anciennes bases préserve les tâches déjà réclamées comme tentées. Les attentes concurrentes et leur arrêt sont bornés. Le paquet embarque aussi les modules transitifs nécessaires aux outils de routage et de quotas.

L'intégration reprend le travail Claude de l'issue #79, complété par une revue des états incertains, des tests de concurrence, une option d'intégration gérée et une vérification du paquet relocalisé. Un essai réel supervisé dans Claude Desktop a confirmé le retour `await_task` → `submit_result` et son acquittement. Il ne démontre ni le réveil autonome d'une conversation froide ni l'isolation par identité de chat. `dispatch_supported=false` pour le daemon testé ; la livraison de cet essai passe par MCP.

Vérification : 1 032 tests exécutés, dont 10 ignorés, sans échec dans la suite adaptée ; les deux tests de socket ignorés ont réussi séparément hors sandbox et le test du catalogue OpenRouter a été exécuté séparément. La suite standard non adaptée n'a pas été déclarée verte dans le sandbox.

Pour construire un bundle autonome, suivre [BUILD-AND-CI](agent-resume/BUILD-AND-CI.md) avec l'archive Python épinglée. La construction ne met pas à jour les intégrations déjà installées ; leur activation et leur chargement effectif se vérifient séparément.
