# SDTM mapping specification — SYN-2026-01

Generated from `crf/study_metadata.py`. Every collected item declares its SDTM target at CRF design time.

| CRF form | Item | Label | Type | SDTM target |
|---|---|---|---|---|
| DM | `BRTHDAT` | Date of birth | date | `DM.BRTHDTC` |
| DM | `SEX` | Sex | code | `DM.SEX` |
| DM | `RFICDAT` | Date of informed consent | date | `DM.RFICDTC` |
| DM | `ARM` | Randomised arm | code | `DM.ARMCD` |
| IE | `IEYN` | All eligibility criteria met | code | `IE.IEORRES` |
| IE | `AGEELIG` | Age 18-75 inclusive | code | `IE.IEORRES` |
| VS | `VSDAT` | Assessment date | date | `VS.VSDTC` |
| VS | `SYSBP` | Systolic BP | number | `VS.VSORRES` |
| VS | `DIABP` | Diastolic BP | number | `VS.VSORRES` |
| VS | `PULSE` | Pulse rate | number | `VS.VSORRES` |
| VS | `TEMP` | Temperature | number | `VS.VSORRES` |
| VS | `WEIGHT` | Weight | number | `VS.VSORRES` |
| MH | `MHTERM` | Reported condition | text | `MH.MHTERM` |
| MH | `MHSTDAT` | Start date | date | `MH.MHSTDTC` |
| MH | `MHONGO` | Ongoing | code | `MH.MHENRTPT` |
| EX | `EXSTDAT` | Dose date | date | `EX.EXSTDTC` |
| EX | `EXDOSE` | Dose administered | number | `EX.EXDOSE` |
| EX | `EXADJ` | Dose adjusted | code | `EX.EXADJ` |
| AE | `AETERM` | Adverse event (verbatim) | text | `AE.AETERM` |
| AE | `AESTDAT` | Start date | date | `AE.AESTDTC` |
| AE | `AEENDAT` | End date | date | `AE.AEENDTC` |
| AE | `AESEV` | Severity | code | `AE.AESEV` |
| AE | `AESER` | Serious | code | `AE.AESER` |
| AE | `AEREL` | Relationship to study drug | code | `AE.AEREL` |
| AE | `AEOUT` | Outcome | code | `AE.AEOUT` |
| AE | `AEACN` | Action taken with study drug | text | `AE.AEACN` |
| CM | `CMTRT` | Medication (verbatim) | text | `CM.CMTRT` |
| CM | `CMINDC` | Indication | text | `CM.CMINDC` |
| CM | `CMSTDAT` | Start date | date | `CM.CMSTDTC` |
| DS | `DSCOMP` | Completed study | code | `DS.DSDECOD` |
| DS | `DSTERM` | Reason for discontinuation | text | `DS.DSTERM` |
| DS | `DSSTDAT` | Date of completion / discontinuation | date | `DS.DSSTDTC` |
