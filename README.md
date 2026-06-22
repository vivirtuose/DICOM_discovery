# DICOM Discovery — EpiBrainRad

Dépôt regroupant les deux générations d'outils de découverte et de QC des fichiers DICOM RT de la cohorte EpiBrainRad.

## Structure du dépôt

```
file_discovery_package/
├── file_discovery/        # Version stable — utilisée en production
├── DICOM_discovery/       # Version en développement — package Python structuré
├── outputs/               # Sorties générées sur la cohorte réelle
│   ├── run_epibrainrad/   # Run complet cohorte EpiBrainRad
│   ├── run_all_patients/  # Run tous patients (RT integrity + rapports)
│   └── run_test_3patients/# Run de validation sur 3 patients
└── _archive/              # Code périmé conservé pour référence
    ├── file_discovery_BACKUP_20260612/
    └── file_discovery_package_audit/
```

---

## `file_discovery/` — Version stable (production)

Scanner DICOM modulaire, opérationnel sur le NAS RADIO-AIDE. Utilisé pour produire les inventaires patients et les rapports de complétude RT.

**Modules :**

| Fichier | Rôle |
|---|---|
| `file_discovery_runner.py` | Point d'entrée principal — lance le scan et produit les sorties |
| `reports.py` | Génération des rapports HTML et CSV |
| `rt_integrity.py` | Vérification de la complétude des fichiers RT (CT, RTDOSE, RTPLAN, RTSTRUCT) |
| `epibrainrad_legacy/` | Modules hérités de la version monolithique (discovery, qc, maps, clinical) |

**Commande cohorte globale :**

```bash
python file_discovery_runner.py \
  --nas-root /mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM \
  --workers 3 \
  --out outputs/run_epibrainrad/
```

**Commande test patient :**

```bash
python file_discovery_runner.py \
  --nas-root /mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM \
  --patients "IC 003" \
  --workers 1 \
  --out outputs/run_test_3patients/
```

---

## `DICOM_discovery/` — Version en développement

Package Python structuré (`src/` layout), versionné, avec tests automatisés (144 tests, pytest) et CLI installable via `pip install -e .`.

**Fonctionnalités ajoutées par rapport à `file_discovery/` :**
- Index DICOM par tags (PatientID, Modality, SeriesInstanceUID)
- Verdicts par patient avec provenance horodatée (JSON + schema_version)
- Vérification légère des ROI TG-263
- Rapport de cohorte HTML interactif (timeline, KPI cliniques)
- CLI `dicom-discovery` avec sous-commandes `index`, `rt-check`, `report`, `completeness`

**Installation :**

```bash
cd DICOM_discovery
pip install -e .
```

**Usage rapide :**

```bash
dicom-discovery report --root /chemin/vers/cohorte --out outputs/run_epibrainrad/cohort_report.html
```

**Tests :**

```bash
cd DICOM_discovery
pytest
```

Voir `DICOM_discovery/README.md` pour la documentation complète.

---

## `outputs/` — Sorties de production

Résultats générés par `file_discovery/` sur la cohorte réelle. Ne pas modifier manuellement.

| Dossier | Outil utilisé | Contenu |
|---|---|---|
| `run_epibrainrad/` | `file_discovery` | Inventaires CSV + carte HTML cohorte EpiBrainRad |
| `run_all_patients/` | `file_discovery` | RT integrity CSV + rapports HTML tous patients |
| `run_test_3patients/` | `file_discovery` | Inventaires de validation sur 3 patients |

---

## `_archive/` — Code périmé

| Dossier | Contenu |
|---|---|
| `file_discovery_BACKUP_20260612/` | Version monolithique originale (avant refactoring) |
| `file_discovery_package_audit/` | Documents d'audit et plan de refactoring (juin 2026) |

Ces dossiers sont conservés pour référence historique. Ne pas réutiliser.

---

## Environnement

- Python 3.8 (`file_discovery/`, `DICOM_discovery/`)
- Conda env : `epibrainrad`
- Accès NAS requis pour les runs sur données réelles : `/mnt/NAS2418_RADT/`
