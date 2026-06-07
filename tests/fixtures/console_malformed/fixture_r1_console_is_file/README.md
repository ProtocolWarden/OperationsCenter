# R1 Violation: .console is a file, not a directory

This fixture represents the R1 violation where .console exists but is a file instead of a directory.
The R1 detector should report "count=1" with sample ".console/ exists but is not a directory".
