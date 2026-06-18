# B1 and B2 Custodian Findings Investigation Report

## Executive Summary

**Status**: ✅ **BOTH B1 AND B2 FINDINGS RESOLVED AND VERIFIED**

The PR `fix/boundary-b2-close` (commit 1ec51f7) successfully resolves both the B1 and B2 boundary detector findings. The custodian-multi gate confirms both B1 and B2 are now clean.

---

## Part 1: Understanding B1 and B2 Detector Rules

### B1 Detector: "Tracked file contains a private-repo name"

**Location**: Custodian boundary detector (boundary.py:339-391)

**What it detects**:
- Scans all git-tracked files for substring matches against forbidden private-repo names
- Forbidden names configured via a boundary disclosure artifact (JSON/YAML file)
- Match is case-sensitive, line-by-line
- Returns one finding per match (capped at 8 samples in report, but count includes all)
- Format: `<filepath>:<lineno>: contains '<name>'`

**Configuration**:
- Forbidden names come from `privacy.boundary_artifact` file
- Source: `privacy.boundary_artifact_file` config key or `$REPOGRAPH_BOUNDARY_ARTIFACT_FILE` env var
- Default excluded paths: `.custodian/`, `.console/`, `config/managed_repos/local/**`, `docs/history/**`, `tools/audit/report/**`

**Severity**: MEDIUM

---

### B2 Detector: "Boundary artifact is required but not configured"

**Location**: Custodian boundary detector (boundary.py:394-406)

**What it detects**:
- Verifies that when `privacy.require_boundary_artifact: true` is set in custodian config, a boundary artifact file must be provided
- Fails if `require_boundary_artifact=true` but no artifact file path is configured OR artifact exists but is content-less
- Root cause of B2 firing on every PR: the decoded `REPOGRAPH_BOUNDARY_ARTIFACT_B64` secret had correct structure but zero `forbidden_names`

**Configuration**:
- In `.custodian/config.yaml`: `privacy.require_boundary_artifact: true`
- Artifact path from: `$REPOGRAPH_BOUNDARY_ARTIFACT_FILE` environment variable (set by CI during base64 decode)

**Severity**: MEDIUM

**Boundary Artifact Schema Required Fields**:
```
- schema_kind: "boundary_artifact"
- schema_version: "1.0.0"
- artifact_kind: "boundary_disclosure_artifact"
- forbidden_names: list of private repo names
- source_graph_id: provenance identifier
- generated_at: timestamp
```

---

## Part 2: Root Cause Analysis

### B2 Root Cause (from remediation doc, lines 105-114)

> "OC's CI `audit` job is red on every PR with a single MED **B2** finding. Diagnosis: `detect_b2` emits its generic 'not provided' message only when the artifact loads with **no error but yields zero boundary names** — so the secret-decoded artifact is present and parseable but **content-less** (no boundary names). This is an **infra/secret issue** (`REPOGRAPH_BOUNDARY_ARTIFACT_B64` needs a real disclosure artifact), not a Custodian code bug."

**Summary**: The CI secret `REPOGRAPH_BOUNDARY_ARTIFACT_B64` was a valid base64 string that decoded to a boundary artifact JSON, but the artifact had no `forbidden_names` list.

### B1 Root Cause (from commit message)

> "The headline-finding line named a private repo literally, which the B1 boundary detector correctly flags as a public/private boundary leak once the boundary artifact is configured."

**Summary**: Once B2 was fixed and B1 activated, it found that the `INCOMPLETE_INTEGRATION_REMEDIATION.md` file explicitly named specific private repos in the headline, which B1 correctly flagged as a public/private boundary violation.

---

## Part 3: Changes in This PR

### Change 1: B1 Fix - Scrub Private Repo Names from Documentation

**File**: `docs/design/INCOMPLETE_INTEGRATION_REMEDIATION.md`

**Change**: File added with B1-safe content (line 17 containing generic reference "the two private repos")

**Location**: Line 17 (headline finding section)

**What was fixed**: The remediation documentation file uses a generic reference ("the two private repos") instead of explicitly naming private repository names. This satisfies the B1 boundary detector's requirement that tracked files must not contain literal private-repo names.

**Impact**: Prevents B1 boundary leak detection when the artifact is configured with forbidden private repo names.

**Note on diff format**: This file was added in commit 1ec51f7, not modified. The content was composed correctly from the start to avoid B1 findings once B2 artifact refresh was completed.

---

### Change 2: B2 Fix - Refresh Boundary Artifact Secret

**Documentation of Fix**: `.console/log.md`

**Log Entry**:
> "The `custodian-audit` job was advisory-red on every PR via a single MED B2 finding. Root cause: the `REPOGRAPH_BOUNDARY_ARTIFACT_B64` CI secret decoded to a content-less payload, so `require_boundary_artifact=true` had zero names → B2 fired. **Refreshed the secret to a valid, current boundary disclosure artifact (PrivateManifest@83d600bd; forbidden_names = the 5 private repos).** That activates B1, which then correctly flagged one real leak: the remediation doc's headline line named a private repo literally. Scrubbed it ("the two private repos"). Verified locally: B1+B2 both clean."

**Secret Updated**: `REPOGRAPH_BOUNDARY_ARTIFACT_B64` (GitHub CI secret - not in git)

**New Artifact Source**: PrivateManifest@83d600bd

**Forbidden Names in Artifact**: The 5 private repos (specific names not listed for security, but tracked in artifact)

**Why it doesn't appear in git diff**: CI secrets are stored in GitHub's secure secret management, not in the repository source code.

---

### Change 3: Update Summary Documentation

**Files**:
- `.console/backlog.md`: Updated PR count from 12 to 14 green-gated PRs
- `.console/log.md`: Added comprehensive fix description with artifact reference

---

## Part 4: Verification

### Custodian Gate Results

**B1 and B2 Gate** (The specific findings):
```
custodian-multi --repos . --only B1,B2 --include-deprecated --fail-on-findings
Result: ✅ CLEAN (0 findings)
```

**D12 and DC10 Gates** (Incomplete integration checks):
```
custodian-multi --repos . --only D12,DC10 --include-deprecated --fail-on-findings
Result: ✅ CLEAN (0 findings)
```

**Overall Status**: ✅ **ALL GATES PASS**

---

## Part 5: How B2 Fix Is Documented

Since the CI secret `REPOGRAPH_BOUNDARY_ARTIFACT_B64` is not stored in git, the fix is documented via:

1. **Commit Message** (1ec51f7):
   > "Pairs with refreshing the REPOGRAPH_BOUNDARY_ARTIFACT_B64 CI secret to a valid, current boundary disclosure artifact"

2. **Operational Log** (`.console/log.md`):
   > "Refreshed the secret to a valid, current boundary disclosure artifact (PrivateManifest@83d600bd; forbidden_names = the 5 private repos)"

3. **CI Workflow** (`.github/workflows/custodian-audit.yml`):
   - Shows how the secret is decoded: base64 decode of `REPOGRAPH_BOUNDARY_ARTIFACT_B64` into `REPOGRAPH_BOUNDARY_ARTIFACT_FILE`
   - Set at lines 36-44

4. **Custodian Configuration** (`.custodian/config.yaml`):
   - Lines 1143-1154 show `privacy.require_boundary_artifact: true` and use of `$REPOGRAPH_BOUNDARY_ARTIFACT_FILE`

---

## Part 6: Acceptance Criteria Met

### ✅ Identify what B1 and B2 findings detect
- **B1**: Tracked files containing literal private-repo names (configured via boundary artifact)
- **B2**: Boundary artifact required but not provided OR provided but content-less

### ✅ Locate the remediation documentation
- Located: `docs/design/INCOMPLETE_INTEGRATION_REMEDIATION.md`
- B1 finding: Line 17 explicitly naming specific private repos (now scrubbed)
- B2 findings documented: Lines 105-114 explain root cause and infrastructure fix

### ✅ Understand REPOGRAPH_BOUNDARY_ARTIFACT_B64 secret purpose and validation
- **Purpose**: Base64-encoded boundary disclosure artifact containing forbidden private-repo names
- **Used In**: CI `custodian-audit` workflow (`.github/workflows/custodian-audit.yml`)
- **Validation**: 
  - Decoded from base64 in CI
  - Must contain valid JSON with schema_kind, schema_version, artifact_kind, forbidden_names
  - B2 detector validates it's provided when `privacy.require_boundary_artifact=true`
  - B1 detector uses the `forbidden_names` list to scan tracked files

### ✅ Map current branch changes to which findings they resolve
- **B1**: Resolved by scrubbing explicit private repo names → "the two private repos"
- **B2**: Resolved by refreshing secret to valid artifact with proper forbidden names list
  - **Documentation**: `.console/log.md` and commit message (1ec51f7)
  - **Verification**: Custodian gate shows B1+B2 clean

---

## Conclusion

The PR successfully addresses both B1 and B2 boundary findings:

1. **B1 Fix** ✅: Documentation scrubbed of explicit private repo names
2. **B2 Fix** ✅: CI secret refreshed with valid boundary artifact (documented in log and commit message)
3. **Verification** ✅: Custodian gates B1, B2, D12, DC10 all pass clean
4. **Documentation** ✅: Complete chain of custody from secret update through verification

The only element missing from the git diff (the secret update) is properly documented in the operational log and commit message, establishing clear provenance for the B2 infrastructure fix.
