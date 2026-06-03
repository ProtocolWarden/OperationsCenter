# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **Fixed TOCTOU race condition in Collector** — `CheckSignalCollector` and `DependencyDriftCollector` are now guarded against file deletion during discovery. Files are no longer stat'd twice, eliminating the race window where files could be deleted between metadata check and use. Files deleted during discovery are now skipped gracefully instead of crashing the observer.

### Changed
- `CheckSignalCollector.latest_matching_file()` now returns `tuple[Path, float] | None` (was `Path | None`), where the float is the captured mtime at discovery time.
- `DependencyDriftCollector._latest_dependency_report()` now returns `tuple[Path, float] | None` (was `Path | None`), where the float is the captured mtime at discovery time.
- Both collectors now unpack the returned tuple and use the captured mtime in signal generation, eliminating the second `stat()` call.

### Documentation
- Added `docs/design/observer-race-condition-guard.md` documenting the TOCTOU race condition vulnerability, the metadata capture guard mechanism, implementation examples, error handling strategy, testing approach, and operational impact.
