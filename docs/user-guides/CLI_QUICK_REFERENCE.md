---
status: complete
title: Operations Center Snapshot Validation CLI — Quick Reference
description: Quick reference card for snapshot validation CLI commands, options, and common workflows
version: "1.0"
date: 2026-06-14
---

# Operations Center Snapshot Validation CLI — Quick Reference

**Quick reference card for snapshot validation CLI commands and options.**

---

## Command Summary

```
operations-center-observer-snapshot COMMAND [OPTIONS] [ARGUMENTS]
```

| Command | Purpose |
|---------|---------|
| `validate` | Validate snapshot against configured layers |
| `observe-and-validate` | Collect and validate snapshot immediately |
| `list` | List stored snapshots |
| `show` | Display snapshot contents |
| `compare` | Compare two snapshots (planned) |
| `export` | Export snapshot to file |
| `import` | Import snapshot from file (planned) |
| `cleanup` | Remove old snapshots |

---

## Global Options

```bash
--version          Show version and exit
--log-level LEVEL  Set logging level (debug|info|warning|error)
--debug            Enable debug mode
--help             Show help
```

---

## `validate` Command

**Most common command — validate snapshot against validation layers.**

### Syntax
```bash
operations-center-observer-snapshot validate SNAPSHOT_PATH [OPTIONS]
```

### Essential Options
```bash
--layers LAYERS              Comma-separated layer numbers (1-5) [default: 1,2,3]
--baseline PATH              Baseline snapshot for layer 5 comparison
--format FORMAT              Output format (table|json|markdown|text) [default: table]
--verbose, -v                Show detailed error information
--quiet, -q                  Minimal output
--output, -o PATH            Save report to file
```

### Advanced Options
```bash
--repo-path PATH             Repository path for accuracy checks
--tolerance FLOAT            Global tolerance 0.0-1.0 [default: 0.05]
--coverage-tolerance FLOAT   Coverage-specific tolerance
--test-count-tolerance FLOAT Test count-specific tolerance
--timeout SECONDS            Max seconds for layer 4 [default: 60]
--retry-transient            Auto-retry on transient errors
--max-retries N              Max retry attempts [default: 3]
```

### Environment Variables
```bash
OC_SNAPSHOT_LAYERS=1,2,3,4,5
OC_SNAPSHOT_TOLERANCE=0.05
OC_SNAPSHOT_BASELINE=/path/baseline.json
OC_SNAPSHOT_REPO_PATH=/path/repo
OC_SNAPSHOT_TIMEOUT=60
OC_SNAPSHOT_LOG_LEVEL=info
```

### Examples
```bash
# Fast validation (layers 1-3)
operations-center-observer-snapshot validate snapshot.json

# Full validation (all layers)
operations-center-observer-snapshot validate snapshot.json --layers 1,2,3,4,5

# With baseline (regression detection)
operations-center-observer-snapshot validate snapshot.json --baseline baseline.json

# Save report
operations-center-observer-snapshot validate snapshot.json --output report.json

# Verbose with custom tolerances
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4,5 \
  --tolerance 0.05 \
  --coverage-tolerance 0.10 \
  --verbose

# Via environment
export OC_SNAPSHOT_LAYERS=1,2,3,4,5
operations-center-observer-snapshot validate snapshot.json
```

### Exit Codes
| Code | Meaning |
|------|---------|
| 0 | SUCCESS |
| 1 | VALIDATION_FAILED |
| 2 | NOT_FOUND |
| 3 | LOAD_ERROR |
| 4 | CONFIG_ERROR |
| 5 | FILE_MISSING |

---

## `show` Command

**Display snapshot contents as JSON, YAML, or markdown.**

### Syntax
```bash
operations-center-observer-snapshot show SNAPSHOT_PATH [OPTIONS]
```

### Options
```bash
--field PATH                 Show specific field (e.g., signals.test_signal)
--format FORMAT              Output format (json|yaml|markdown) [default: json]
--pretty                     Color-coded pretty print
--quiet, -q                  Minimal output
```

### Examples
```bash
# Show full snapshot
operations-center-observer-snapshot show snapshot.json

# Show specific field
operations-center-observer-snapshot show snapshot.json --field signals.test_signal

# Pretty YAML
operations-center-observer-snapshot show snapshot.json --format yaml --pretty
```

---

## `list` Command

**List stored snapshots in default or custom directory.**

### Syntax
```bash
operations-center-observer-snapshot list [OPTIONS]
```

### Options
```bash
--limit N                    Max snapshots to list [default: 10]
--order ORDER                Sort order (recent|oldest|name) [default: recent]
--filter STATUS              Filter (valid|invalid)
--format FORMAT              Output format (table|json|csv) [default: table]
--storage-root PATH          Storage directory
--verbose, -v                Include file size, checksum
--quiet, -q                  Minimal output
```

### Examples
```bash
# List 20 most recent
operations-center-observer-snapshot list --limit 20

# List as JSON
operations-center-observer-snapshot list --format json

# List with details
operations-center-observer-snapshot list --verbose
```

---

## `export` Command

**Export snapshot to JSON, YAML, or JSONL file.**

### Syntax
```bash
operations-center-observer-snapshot export SNAPSHOT_ID OUTPUT_PATH [OPTIONS]
```

### Options
```bash
--format FORMAT              Output format (json|yaml|jsonl) [auto-detect from extension]
--quiet, -q                  Minimal output
```

### Examples
```bash
# Export to JSON (auto-detected)
operations-center-observer-snapshot export snapshot-id export.json

# Export to YAML
operations-center-observer-snapshot export snapshot-id export.yaml

# Export to JSONL
operations-center-observer-snapshot export snapshot-id export.jsonl
```

---

## `cleanup` Command

**Remove old snapshots based on age and retention policy.**

### Syntax
```bash
operations-center-observer-snapshot cleanup [OPTIONS]
```

### Options
```bash
--days N                     Delete snapshots older than N days [default: 30]
--keep-count N               Keep at least N most recent [default: 50]
--dry-run/--no-dry-run       Preview changes (default: true)
--storage-root PATH          Storage directory
--quiet, -q                  Minimal output
```

### Examples
```bash
# Preview cleanup
operations-center-observer-snapshot cleanup --days 30 --keep-count 50

# Actually delete (not dry-run)
operations-center-observer-snapshot cleanup --days 30 --keep-count 50 --no-dry-run
```

---

## Common Workflows

### Workflow 1: Quick Validation
```bash
operations-center-observer-snapshot validate snapshot.json
```
**Time**: ~100ms | **Layers**: 1,2,3 | **Use**: Pre-push checks

### Workflow 2: CI Validation
```bash
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4 \
  --repo-path . \
  --timeout 120 \
  --format json \
  --output report.json
```
**Time**: 5-30s | **Layers**: 1,2,3,4 | **Use**: CI pipelines

### Workflow 3: Regression Detection
```bash
operations-center-observer-snapshot validate snapshot.json \
  --layers 1,2,3,4,5 \
  --baseline baseline.json \
  --coverage-tolerance 0.05 \
  --test-count-tolerance 0.05
```
**Time**: 10-60s | **Layers**: 1,2,3,4,5 | **Use**: Baseline comparison

### Workflow 4: Debugging
```bash
operations-center-observer-snapshot validate snapshot.json \
  --verbose \
  --output report.json \
  --format json
```
**Time**: Variable | **Format**: JSON | **Use**: Troubleshooting

---

## Troubleshooting Quick Links

| Problem | Solution |
|---------|----------|
| File not found (exit 2) | Verify path with `ls -la` or use `list` command |
| Parse error (exit 3) | Validate JSON/YAML: `python -m json.tool file.json` |
| Validation failed (exit 1) | Use `--verbose` to see detailed errors |
| Timeout | Increase `--timeout` or skip layer 4 with `--layers 1,2,3` |
| Tool not found | Install: `pip install pytest ruff coverage` |
| Invalid option | Check option spelling or run `--help` |

---

## Environment Variables Reference

```bash
OC_SNAPSHOT_LAYERS              # Validation layers (1,2,3,4,5)
OC_SNAPSHOT_TOLERANCE          # Global tolerance (0.05)
OC_SNAPSHOT_BASELINE            # Baseline path
OC_SNAPSHOT_REPO_PATH           # Repository path
OC_SNAPSHOT_TIMEOUT             # Layer 4 timeout (60)
OC_SNAPSHOT_COVERAGE_TOLERANCE # Coverage tolerance
OC_SNAPSHOT_TEST_COUNT_TOLERANCE # Test count tolerance
OC_SNAPSHOT_LOG_LEVEL           # Logging level
```

---

## Help & Documentation

```bash
# Show version
operations-center-observer-snapshot --version

# Show help for command
operations-center-observer-snapshot validate --help

# Show help for main app
operations-center-observer-snapshot --help

# View detailed user guide
cat docs/user-guides/SNAPSHOT_VALIDATION_CLI_GUIDE.md
```

---

## Tolerance Guidelines

| Metric | Conservative | Default | Aggressive |
|--------|--------------|---------|-----------|
| **Test Count** | 0.01 (1%) | 0.05 (5%) | 0.10 (10%) |
| **Coverage** | 0.02 (2%) | 0.05 (5%) | 0.10 (10%) |
| **Lint** | 0.05 (5%) | 0.10 (10%) | 0.20 (20%) |

---

## Exit Code Reference

```bash
0 - SUCCESS               # All validation passed
1 - VALIDATION_FAILED     # One or more layers failed
2 - NOT_FOUND             # Snapshot file not found
3 - LOAD_ERROR            # Failed to parse snapshot
4 - CONFIG_ERROR          # Invalid option or config
5 - FILE_MISSING          # Required file missing
```

---

## Validation Layers at a Glance

| Layer | Speed | Checks | When to Use |
|-------|-------|--------|-------------|
| **1** | 50ms | JSON/YAML structure | Always (baseline) |
| **2** | 20ms | Required signals | Data completeness |
| **3** | 50ms | Signal consistency | Logical correctness |
| **4** | 5-30s | Real tool values | Accuracy validation |
| **5** | 5-30s | vs. baseline | Regression detection |

---

## Output Format Comparison

| Format | Use Case | Speed |
|--------|----------|-------|
| `table` | Human-readable (CLI) | Fast |
| `json` | Machine parsing, CI | Fast |
| `markdown` | Documentation | Fast |
| `text` | Plain text output | Fast |

---

## Installation

```bash
# From OperationsCenter repo
pip install -e .

# Verify installation
operations-center-observer-snapshot --version

# Check tools for layer 4
which pytest ruff coverage
```

---

## Getting Help

```bash
# Full user guide
docs/user-guides/SNAPSHOT_VALIDATION_CLI_GUIDE.md

# Design documentation
docs/design/STAGE0_CLI_SPECIFICATION.md

# CLI help
operations-center-observer-snapshot --help
operations-center-observer-snapshot validate --help

# Report an issue
Visit: https://github.com/ProtocolWarden/OperationsCenter/issues
```

---

**Version**: 0.1.0  
**Last Updated**: 2026-06-14
