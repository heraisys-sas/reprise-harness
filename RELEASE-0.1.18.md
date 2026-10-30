# Reprise 0.1.18 — collecte et échéance Claude

La version commune passe à 0.1.18 pour l'application, les helpers et les connecteurs. L'installateur et le manifeste consomment `agent-resume/VERSION`.

Dans Intégrations, la boîte de réception permet de lister les tâches Claude du projet autorisé, de lire un résultat vérifié par empreinte et de l'acquitter explicitement. Le connecteur propose également d'activer ou désactiver `await_task`, ou de conserver le réglage existant. La livraison incertaine n'est pas rejouée ; un résultat accepté par `submit_result` reste le critère de fin d'une tâche MCP.

Le suivi Claude peut présenter une échéance relevée dans le journal local, avec sa source et ses limites. Le journal n'établit ni le compte ou le chat concernés, ni un pourcentage, ni la disponibilité actuelle du quota. Une échéance dépassée ne prouve pas que la reprise a eu lieu.

Le bundle contient le nouveau code, mais la construction ou la mise à jour de l'application ne prouve pas que Claude Desktop a rechargé le connecteur ni que le daemon existant a été remplacé. Un ancien daemon peut ne pas fournir la liste des tâches ; la mise à jour gérée et les vérifications du chargement sont des étapes distinctes. Le transport CDP et la reprise après quota restent soumis à leurs propres validations.

La sortie directe de `claude_quota_log.py` adopte le format d'observation normalisé. Sans fuseau attesté du producteur, l'heure de l'événement reste inconnue ; le timestamp absolu `resetsAt` reste distinct de cette incertitude.
