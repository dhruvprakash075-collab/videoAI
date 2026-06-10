# Skylos Full Scan Results

**Scan Date:** 2026-06-10  
**Tool:** Skylos v4.24.1  
**Project:** Video.AI  
**Scan Type:** Full scan with all checks (-a)

## Summary

- **Total Issues:** 3,049
- **Files Analyzed:** 230

## Issue Breakdown

### Dead Code
- **Unused Functions:** 32
- **Unused Imports:** 7
- **Unused Parameters:** 32
- **Unused Variables:** 17
- **Unused Classes:** 1
- **Empty Files:** 4 Python + 1 TypeScript

### Security (Danger)
- **Security Issues:** 628

### Secrets
- **Secrets Found:** 113

### Quality
- **Quality Issues:** 2,032

### Dependencies
- **Vulnerabilities:** 182

## Top Problem Areas

Based on the scan, the following files have the most issues:

1. **core/segment_runner.py** - Multiple unused functions, parameters, and variables
2. **video/image_gen/comfyui_workflow.py** - Unused functions and imports
3. **video/image_gen/image_gen.py** - Unused functions and imports
4. **audio/audio_proxy.py** - Many unused parameters
5. **studio_tui.py** - Unused functions and variables

## Recommendations

1. **Immediate:** Review and clean up dead code in `core/segment_runner.py` (15+ issues)
2. **Security:** Address 628 security findings, especially in critical files
3. **Secrets:** Review 113 potential secret exposures
4. **Dependencies:** Update packages with 182 vulnerabilities

## Output Files

- JSON report: `skylos_results.json`

## Notes

- Windows terminal encoding issues prevented full pretty-print output
- Use `skylos . -a --format concise` for machine-readable output
- Use `skylos . -a --format json` for full structured report
