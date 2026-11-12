# Archive du proxy planner ASI1 — 28 octobre 2026

Retiré à la demande explicite de l’opérateur après confirmation, rapportée par celui-ci, d’un employé CUDOS : le planner serveur n’est pas implémenté chez ce fournisseur. Cette confirmation ne porte pas sur les appels d’outils ordinaires, qui restent utilisables par le harness en accès direct.

L’archive contient les implémentations historiques Node/Python, leurs tests, recettes launchd, revue de sécurité et fichiers de contexte de la baseline `940ccbb34749f66c6572bc3a29b6643070aabd8f`. Le manifeste donne la liste et le SHA-256. Aucun secret, journal utilisateur ou configuration personnelle n’a été ajouté à cette archive Git. Ces sources sont exclues du produit, du packaging et de la découverte des tests actifs.

Pour une étude ultérieure, vérifier l’empreinte puis extraire dans un dossier isolé. La baseline Git conserve aussi l’ensemble des dépendances et du contexte. Ne pas réinstaller les anciennes recettes : elles supposent une capacité fournisseur absente aujourd’hui. Une réactivation éventuelle demanderait une nouvelle confirmation fournisseur et des validations ciblées.

Le retrait local du service est distinct de la publication du code. Ses sauvegardes privées (configuration, plist, gestion et journaux dédiés) restent hors Git. Les releases immuables partagées avec d’autres services ne sont pas modifiées ni supprimées.
