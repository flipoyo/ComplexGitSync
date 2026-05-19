# CorPlan — Cartographie des classes et plan de correction minimaliste

## 1) Livrable de cartographie

- Fichier PNG: `docs/figures/class_interactions_map.png`
- Vue synthétique incluse:
  - noyau objet (`GitRepo`, `GitTree`, `DependencyTreeRegistry`)
  - orchestration (`Orchestre`, `ComplexGitSyncClient`, `GitRunner`, `RuntimeStateStore`)
  - couche documentaire (`ConfigDocument`, `CgsDocument`, `GtsDocument`, `GocDocument`)
  - interactions principales (ownership, usage, mutation)

## 2) Objets potentiellement inutiles (à confirmer)

Analyse basée sur les références dans `src/ComplexGitSync`:

1. `ArchitectureNotLoadedError` (`src/ComplexGitSync/errors.py`)
   - défini mais non référencé hors de sa déclaration.
2. `FallbackRejectedError` (`src/ComplexGitSync/errors.py`)
   - défini mais non référencé hors de sa déclaration.
3. `RepoNode` (`src/ComplexGitSync/git_repo.py`)
   - classe exposée/importée mais pas d’instanciation détectée dans le code applicatif (`src/ComplexGitSync`, motif `RepoNode(...)` absent).

> Note: ces éléments peuvent rester utiles pour compatibilité API/doc. Le plan ci-dessous vise un package plus minimal sans rupture brutale.

## 3) Plan de correction (minimal package)

### Étape A — Sécuriser par tests de non-régression
- Ajouter des tests ciblés:
  - import public (`from ComplexGitSync import ...`) pour valider l’API exportée.
  - couverture des exceptions réellement levées en runtime.

### Étape B — Dépréciation douce (1 release)
- Marquer `ArchitectureNotLoadedError` et `FallbackRejectedError` comme dépréciées dans la doc/API.
- Documenter `RepoNode` comme « interne / non utilisé en runtime » si confirmé.

### Étape C — Simplification effective
- Si aucune dépendance externe observée:
  - retirer les exceptions non utilisées,
  - retirer `RepoNode` de `__all__` (ou le conserver en alias de compatibilité temporaire),
  - nettoyer la documentation d’architecture pour refléter le modèle réellement exécuté.

### Étape D — Validation finale
- Exécuter `python -m pytest`.
- Vérifier la CLI (`cgitsync --help` + commandes principales) pour garantir l’absence de régression fonctionnelle.

## 4) Résultat attendu

- Surface API plus compacte.
- Moins d’objets passifs/non utilisés.
- Documentation alignée sur le runtime réel.
