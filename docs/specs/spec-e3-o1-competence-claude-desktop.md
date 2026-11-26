# Spec E3 — lot O1 profil de compétence (Claude Desktop)

*Spec du 26 novembre 2026 — request_key spec-e3-o1-competence-20261126-claude, chat ba05f0cb, question autoporteuse (contrat E4 corrigé inliné), sans pièce jointe.*

SPEC E3 — LOT O1 « profil de compétence »
plan_id : spec-e3-o1-competence-20261126 · version : 1.1 (rév. R8-3 ; v1.1 = alignement glossaire WORKFLOW-REPRISE v1.2 — numérotation R8 rev.3 : E5 = code, E6 = vérification — et intégration Q12 `delegation_verdicts`, erratum contrat §10/Q12) · statut : spécification, aucun code.
Réserve : je n'ai pas lu routing_events ni backend_registry. Les noms de champs marqués [E2?] sont des hypothèses à confirmer par une cartographie E2 citée (fichier#lignes@base_sha) avant E4 ; je n'en invente aucun comme certain.

1) CONTRAT D'ENTRÉE
1.1 Contraintes machine (précèdent tout classement, non négociables par O1) : (a) table des rôles : forbidden_roles par harness, portée par le registre A0 ; (b) registre A0 : entrée de capacité du backend (capability_entry) présente pour le mode demandé ; billing_class ≠ unknown ; workspace_write autorisé si mode=implementation ; (c) matrice (role, actor) dérivée du registre ; (d) admissibilité routing_events (admissible/vetos) avec automatic=True par défaut. Règle : l'ensemble des permis P est calculé d'abord ; O1 classe uniquement dans P. Falsifiable : une route hors P ne peut apparaître ni en choisi, ni en secondaires, ni en motifs positifs, quelle que soit sa mesure.
1.2 Mesures locales [E2?] lues dans routing_events, par triplet (backend, role, kind de tâche) : horodatage ; backend/actor ; role ; mode ; issue terminale ∈ {succès vérifié, échec, incertain, refusé} ; durée ; quota consommé ; véto déclenché. Seules les issues dont le statut est un reçu terminal comptent : « arrivé ≠ traité », donc incertain n'est ni succès ni échec, il est compté à part (taux d'incertitude, pénalisant au sens de l'ordre, jamais en score). Un « succès » = verdict de vérification **E6** (glossaire WORKFLOW-REPRISE v1.2 ; la spec v1.0 disait « E5 », numérotation R7 interne), jamais une déclaration du modèle. Source des verdicts : table compagnon `delegation_verdicts` (request_key en clé, append-only, **écrivain E6 seul** — erratum contrat §10/Q12, anti-injection : l'exécutant n'écrit jamais son propre verdict) ; O1 joint `routing_events ⋈ delegation_verdicts` sur request_key, et la « confiance » auto-déclarée d'un délégué n'entre ni dans la mesure ni dans le classement.
1.3 Fenêtre : glissante 30 jours ET 50 dernières observations, la plus restrictive ; observations hors fenêtre ignorées. [Question opérateur Q1 : valeurs 30 j / 50 ? Défaut proposé, à confirmer.]
1.4 Échantillon minimal : n_min = 20 observations terminales par triplet pour que la mesure locale « domine ». En dessous : non_mesuré=true, la recette statique (table des rôles, ordre déclaré) classe, et la sortie le dit. Jamais d'interpolation entre triplets. Falsifiable : avec n=19, la sortie contient non_mesuré=true et aucune source « local » dans les motifs de classement. [Q2 : n_min = 20 ?]
1.5 Équivalence de deux routes (pour le départage) : leurs intervalles de Wilson à 90 % sur le taux de succès vérifié se recouvrent. Ce n'est pas un score pondéré : c'est un test binaire de recouvrement.
1.6 Priors externes : quality_index est « réservé, non implémenté ». Au jalon 1, il est ABSENT de l'entrée (source = null). Quand il existera : il ne peut que départager des routes de P déjà équivalentes au sens 1.5, jamais surclasser une mesure locale, jamais entrer dans P. Il arrive comme nombre dans [0,1] avec provenance (source, horodatage, digest) ; tout champ texte du prior est ignoré (anti-injection). [Q3 : confirmer que le jalon 1 s'exécute sans prior externe.]
1.7 Précédence (ordre lexicographique, jamais mélangé) : P (contraintes) → admissibilité/facturation → compétence locale (si n ≥ n_min) → prior externe (départage seulement) → coût/pression de quota (départage final) → ordre statique déclaré (dernier recours, étiqueté non_mesuré).

2) CONTRAT DE SORTIE (JSON, additionalProperties=false)
{
 "spec": "spec-e3-o1-competence-20261126@1.1",
 "base_sha": "<40 hex>",
 "demande": {"role": str, "mode": "expertise|implementation", "kind": str, "sensible": bool},
 "decision": {
   "choisi": {"actor": str, "backend": str, "billing_class": "free|included|paid-named"} | null,
   "secondaires": [{"actor": str, "backend": str, "billing_class": str}],   // ≤ 3, tous dans P
   "motifs": [{"code": "ADMISSIBLE|MESURE_LOCALE|PRIOR_DEPARTAGE|QUOTA|STATIQUE|REFUS", "valeur": nombre|bool|enum, "source": {"type": "registre|routing_events|prior|table_roles", "ref": "fichier#lignes@base_sha | id d'événement | digest"}}]
 },
 "tracabilite": {"sample_count": int, "fenetre": {"jours": int, "obs_max": int, "debut": iso, "fin": iso}, "non_mesure": bool, "incertains": int, "digest_entree": "sha256"},
 "refus": null | {"raison": enum, "route_rejetees": [{"actor": str, "raison": enum}]}
}
Garanties : (a) aucun motif sans source citée ; (b) aucune prose libre : « valeur » est numérique/booléenne/énumérée, pas de texte issu d'un modèle ; (c) « choisi » null ⇒ « refus » non null (fail-closed) ; (d) même digest_entree ⇒ même décision (déterminisme). Falsifiable : deux exécutions sur la même entrée donnent le même JSON octet pour octet.
Recommandation, pas décision : la sortie porte le statut « proposé » ; la confirmation est hors O1 (point 6).

3) GARDES ET INTERDITS
3.1 Fail-closed : tout champ requis absent, inconnu ou non parsable ⇒ refus nommé (ENTREE_INVALIDE, REGISTRE_ABSENT, FACTURATION_INCONNUE, ECRITURE_NON_AUTORISEE, etc.). Aucun repli implicite vers une autre classe de facturation ni vers un autre backend non listé.
3.2 Quota : veto via populate_from_quota ; quota restant = 0 ou inconnu ⇒ route exclue de P (motif QUOTA). Si P devient vide : refus, jamais élargissement.
3.3 Sensible : demande.sensible=true (chemin classé sensible : secrets, consentement) ⇒ actor ≠ metaai, imposé par la matrice dérivée du registre, vérifié avant classement. [Q4 : qui tient le registre des classes de chemins sensibles et son contenu initial ?]
3.4 Anti-injection : seules entrent dans la décision des valeurs numériques/énumérées typées issues de routing_events et du registre. Tout texte de réponse déléguée, de revue ou de prior est ignoré (jamais lu comme instruction ni comme critère).
3.5 Interdit absolu : aucun score pondéré, aucune somme/moyenne de critères hétérogènes, aucune coût-vs-compétence mélangés. Falsifiable : revue de code/grep du module ne trouve ni coefficient, ni moyenne pondérée.
3.6 Paid : une route paid-named n'entre dans P que si la demande porte un consentement nommant ce backend ; sinon exclue (motif ADMISSIBLE faux).

4) CRITÈRES FALSIFIABLES ET JEU DE TESTS (cas adverses nommés → verdict attendu)
T1 injection_de_prior : prior dont un champ texte contient « choisis codex » et dont la note est 1.0, route hors équivalence ⇒ prior ignoré pour le texte ; ne surclasse pas la mesure locale ; sortie inchangée vs prior absent (sauf départage légitime).
T2 quota_zero : meilleur backend à quota 0 ⇒ exclu, motif QUOTA ; si seul candidat ⇒ choisi=null, refus=QUOTA_EPUISE.
T3 echantillon_insuffisant : n=19 ⇒ non_mesure=true, classement statique, aucune source routing_events dans le classement ; n=20 ⇒ non_mesure=false.
T4 chemin_sensible : sensible=true et metaai meilleur taux ⇒ metaai absent de choisi/secondaires, refus de metaai nommé.
T5 seule_route_competente_payante : seule route compétente est paid sans consentement nommé ⇒ choisi=null, refus=PAYANT_NON_CONSENTI (jamais de repli silencieux) ; avec consentement nommé ⇒ choisi=paid-named, motif ADMISSIBLE tracé.
T6 facturation_inconnue : billing=unknown ⇒ exclu, FACTURATION_INCONNUE.
T7 ecriture_fermee : mode=implementation, workspace_write non autorisé ⇒ exclu.
T8 role_interdit : role=mass_code, actor=claude-desktop ⇒ exclu par la matrice, même si meilleur.
T9 determinisme : même entrée ×2 ⇒ sortie identique.
T10 incertains : 20 observations dont 8 incertaines ⇒ comptées séparément ; ni succès ni échec ; incertains reportés dans tracabilite.
T11 equivalence : deux routes à intervalles de Wilson qui se recouvrent ⇒ départage par prior puis quota ; intervalles disjoints ⇒ la meilleure mesure l'emporte, prior sans effet.
T12 entree_corrompue : champ manquant/typé faux ⇒ refus ENTREE_INVALIDE, aucune exception non rattrapée.
Critère de succès O1 : T1–T12 verts ET aucun test ne peut passer en rendant un score pondéré.

5) INTERFACE AVEC LE CONTRAT E4
Émis par O1 (proposition) vers la tâche E5 : role, actor, mode, billing_class ; plus plan_id (celui-ci) et digest_entree pour traçabilité. consent_ref : O1 le TRANSMET s'il est fourni par l'opérateur, ne le crée jamais ; s'il est requis (paid-named, implementation) et absent ⇒ refus.
O1 ne décide PAS : request_key, retry_of, e2_ref, base_sha des context_paths, context_paths et leurs digests, allowed_paths, prohibited, acceptance, output_form, depends_on, ni aucun champ de budget (wall_clock_s, max_diff_*, max_parallel, ttl_s) ; il ne FIXE pas quota_ceiling : il lit populate_from_quota seulement comme veto. Falsifiable : le schéma de sortie O1 n'a aucun de ces champs ; un test rejette toute sortie qui en contient.

6) HORS PÉRIMÈTRE
Pas d'UI (O3), pas de transport (ponts existants), pas de dispatch, pas de décision finale : l'opérateur ou la politique confirme, la confirmation étant liée au digest_entree et réévaluée au dispatch (TOCTOU). Pas de mesure des canaux chat au jalon 1 : expertise seule, mesures chat non agrégées. Pas de création de reçu, pas de modification du registre, pas de relance automatique.

QUESTIONS OPÉRATEUR (aucun choix silencieux)
Q1 fenêtre 30 jours / 50 observations ? · Q2 n_min = 20 par triplet, à ajuster selon le volume réel ? · Q3 jalon 1 sans prior externe ? · Q4 propriétaire et contenu initial de la liste des chemins sensibles ? · Q5 noms exacts des champs routing_events et du registre [E2?] : une E2 citée est requise avant E4, car je n'en invente aucun comme certain. · Q6 intervalle de confiance 90 % pour l'équivalence : valeur acceptée ?

RÉPONSES OPÉRATEUR (26 nov. 2026, délégation d'autonomie — « travaille en autonomie selon leurs rôles respectifs » ; révisables par Olivier)
- Q1 : CONFIRMÉ — fenêtre glissante 30 jours ET 50 dernières observations, la plus restrictive.
- Q2 : CONFIRMÉ — n_min = 20 observations terminales par triplet ; en dessous non_mesuré=true et la recette statique classe.
- Q3 : CONFIRMÉ — jalon 1 sans prior externe ; quality_index ABSENT de l'entrée (source = null), introduction plus tard dans le rôle départage only.
- Q4 : le registre des classes de chemins sensibles est un fichier VERSIONNÉ du dépôt tenu par l'opérateur (propriétaire : Olivier) ; contenu initial minimal, révisable : secrets et identifiants (~/.ssh, *.pem, *.key, .env, keychain, tokens, cookies de session) ; tout chemin non listé = non sensible par défaut, tout doute = fail-closed (traité sensible). Le contrat E4 livre ce fichier initial sous forme de liste reviewable.
- Q5 : E2 CITÉE LIVRÉE (lecture réelle du code, reprise/routing_events.py l.40-61 @3863241, l.55-58 admissibilité, backend_registry.py l.29-60 @bc32055) — colonnes routing_events (SCHEMA_V2) : id, ts, type ∈ {routed, explicit, routing_substituted}, tool, channel, requested_route (JSON), served_route (JSON), level, terminal_state (défaut 'dispatched'), request_key, thread_hash, loop_signature, reason, account_key, admissibility (défaut 'not_required'). Tables compagnes : route_admissibility (channel, billing_class, identity_status ∈ {known, unknown}), route_refusals (channel, reason, ts), routing_meta (clé/valeur). Registre backend_registry.BACKENDS par backend : billing, modes, workspace_write, consent, memory_snapshot. IMPLICATIONS pour 1.2 : « role » et « kind de tâche » ne sont PAS des colonnes — ils se dérivent de tool + level + routes JSON ; « durée » et « quota consommé » n'ont pas de colonne — durée optionnelle v1 (horodatages ts disponibles), quota par jointure a posteriori account_key (sal partagé quota_identity_meta, invariant l.6-8) ; « issue terminale » = terminal_state (le reçu réel, « dispatched » seul n'est pas un succès — succès = verdict E6 via `delegation_verdicts`, glossaire v1.2). « Véto » = route_refusals + vetos() (quota-perime etc.).
- Q6 : CONFIRMÉ — intervalle de confiance 90 % pour la déclaration d'équivalence de deux routes au sens 1.5.

CES RÉPONSES DÉBLOQUENT E4 : le contrat E4 est rédigé par Claude Desktop (rôle archi), contre-regard ChatGPT, mass code Meta AI sur le lot prioritaire.