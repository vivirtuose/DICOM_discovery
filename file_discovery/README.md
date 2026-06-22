# file_discovery_runner — scanner de serveurs médicaux/recherche, institution-agnostic

Runner **autonome** (un seul fichier, aucune dépendance au package d'origine) qui scanne
**n'importe quelle racine** (ou liste de racines), sans hypothèse sur l'organisation du
serveur, et produit toujours :

- des **inventaires CSV** (fichiers, dossiers, patients, DICOM, modalités, manquants, doublons, ambiguïtés, erreurs) ;
- un **résumé JSON** ;
- un **rapport QC Markdown** ;
- une **carte interactive HTML autonome** (Plotly embarqué — s'ouvre hors-ligne, sans serveur ni CDN).

> Remplace le package EpiBrainRad-spécifique `notebooks/file_discovery/` par une version
> généralisée : aucun chemin codé en dur, identifiants patients configurables par regex/profil.

---

## Installation / dépendances

Aucune installation : c'est un script Python unique. Dépendances :

| Paquet | Requis | Rôle |
|---|---|---|
| `pandas` | **oui** | inventaires |
| `pydicom` | optionnel | classification DICOM fine (sinon : extension/magic) |
| `plotly` | optionnel | carte interactive (sinon : carte HTML statique de secours) |

Toutes présentes dans l'env conda **`epibrainrad`** :
```bash
PY=/home/vmetzger/miniconda3/envs/epibrainrad/bin/python
```

---

## Démarrage rapide

```bash
$PY file_discovery_runner.py \
  --root "/chemin/vers/mon/serveur" \
  --out-dir "/chemin/vers/sortie" \
  --make-map
```

Carte générée : `<out-dir>/interactive_file_discovery_map.html` (double-clic, hors-ligne).

---

## Exemples

### 1. Générique (aucune hypothèse)
```bash
$PY file_discovery_runner.py \
  --root /path/to/server/root \
  --out-dir /path/to/output \
  --patient-regex "IC[\s_-]?\d{3}" \
  --make-map
```

### 2. Plusieurs serveurs / institutions à la fois
```bash
$PY file_discovery_runner.py \
  --root /server1/DICOM --root /server2/MRI --root /server3/clinical \
  --out-dir /out --profile generic --make-map
```

### 3. EpiBrainRad (NAS complet)
```bash
$PY file_discovery_runner.py \
  --root "/mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM" \
  --out-dir outputs/file_discovery_nas_radioaide \
  --profile epibrainrad --follow-symlinks --make-map
```

### 4. Test rapide sur quelques patients
Le runner n'a pas de filtre `--patients` : on cible quelques patients en passant
directement leurs dossiers comme racines (`--root` répétable).
```bash
$PY file_discovery_runner.py \
  --root "/mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM/IC 003" \
  --root "/mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM/IC 005" \
  --root "/mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM/IC 010" \
  --out-dir outputs/file_discovery_test_3patients \
  --profile epibrainrad --make-map
```

### 5. Gros NAS — premier passage rapide
```bash
$PY file_discovery_runner.py \
  --root "/mnt/.../DICOM" --out-dir /out \
  --profile epibrainrad --no-dicom --max-depth 2 --make-map
```

---

## Options

| Option | Effet |
|---|---|
| `--root PATH` | racine à scanner (**répétable** pour plusieurs serveurs) — requis |
| `--out-dir PATH` | dossier de sortie — requis |
| `--patient-regex RE` | regex d'ID patient (**répétable**) ; surcharge le profil |
| `--profile NAME` | `generic`, `epibrainrad`, `dicom_rt`, `neuroimaging`, `clinical_excel` |
| `--id-template` | gabarit d'ID, ex `"IC {num:03d}"` |
| `--make-map` | carte interactive HTML (treemap structurel autonome) |
| `--cohort-map` | carte cohorte longitudinale (date × patient × modalité) |
| `--rt-check` | intégrité de la chaîne RT (FoR, chaînage RTPLAN→RTSTRUCT→RTDOSE, ROI GTV/CTV/PTV) → `df_rt_integrity.csv` + panneau dans la carte cohorte |
| `--report` | **dashboard HTML « software »** (`file_discovery_report.html`) : en-tête, KPI cliquables, onglets, tableau RT triable/filtrable/exportable. Implique la carte cohorte + l'intégrité RT |
| `--workers N` | workers pour le scan cohorte (NAS : 2-3) |
| `--include-clinical` / `--clinical-xlsx` | fusionne l'Excel clinique dans la carte cohorte |
| `--no-dicom` | n'ouvre aucun header DICOM (rapide) |
| `--max-dicom-headers N` | budget global de lectures de headers (défaut 20000) |
| `--max-depth N` | profondeur max de descente (défaut 64) |
| `--follow-symlinks` | suit les liens symboliques (montages NAS) |
| `--verbose` | logs détaillés |

---

## Profils (défauts d'ID patient)

| `--profile` | Regex par défaut | Usage |
|---|---|---|
| `generic` | lettres?+chiffres, sub/patient/case… | aucune hypothèse |
| `epibrainrad` | `\bIC[ _-]?(\d{1,3})\b` + gabarit `IC {num:03d}` | Institut Strauss |
| `dicom_rt` | `[A-Za-z]{1,6}[ _-]?\d{2,4}` | exports CT+RT |
| `neuroimaging` | `sub-XXX`, IRM | études type BIDS |
| `clinical_excel` | id large | fichiers cliniques |

---

## Sorties (dans `--out-dir`)

```
inventories/
  df_files.csv  df_folders.csv  df_patients.csv  df_dicom.csv
  df_modalities.csv  df_missing_by_patient.csv  df_duplicates.csv
  df_ambiguous.csv  df_errors.csv
discovery_summary.json
qc_report.md
interactive_file_discovery_map.html      ← carte autonome (treemap racine → patient → catégorie)
```

QC calculé : RTDOSE sans RTSTRUCT, RTSTRUCT sans RTDOSE, CT sans RTSTRUCT, IRM sans masque,
clinique sans imagerie, fichiers vides, IDs ambigus, erreurs d'accès.

---

## Bonnes pratiques NAS

- Gros montage lent → commencer par `--no-dicom --max-depth 2` puis affiner.
- Le runner **n'ouvre jamais** les fichiers à extension connue non-DICOM (archives `.zip`,
  Excel…) ; le test du magic `DICM` est limité aux fichiers **sans extension**.
- Scan long → lancer avec `nohup … &` ou dans `tmux`/`screen`.
- Le scan **continue malgré les erreurs** d'accès (consignées dans `df_errors.csv`) et la
  carte est **toujours générée**, même partiellement.

---

## Limites connues

- Carte = vue **structurelle** (treemap). La timeline DICOM par patient de l'ancien package
  n'est pas reportée ici.
- Logique clinique EpiBrainRad (Excel CRF/CSCT) non incluse : à brancher via un profil.
- Voir `../file_discovery_package_audit/refactoring_plan.md` pour l'éclatement en package
  modulaire complet (`config/scanner/classifiers/profiles/tests`).
```
