.PHONY: catalog validate check audit evals eval-harness benchmark test

catalog:
	python3 scripts/build-provenance.py
	python3 scripts/build-skill-audit.py
	python3 scripts/build-catalog.py
	python3 scripts/build-evals.py
	python3 scripts/build-catalog-enrich.py

# Catalog/provenance/audit/eval freshness + repo link & frontmatter validation.
validate:
	python3 scripts/validate-repo.py
	python3 scripts/validate-workflows.py
	python3 scripts/build-provenance.py --check
	python3 scripts/build-skill-audit.py --check
	python3 scripts/build-catalog.py --check
	python3 scripts/build-evals.py --check
	python3 scripts/build-catalog-enrich.py --check

# Declarative flagship eval prompt matrix (docs/EVALS.md).
evals:
	python3 scripts/build-evals.py

# Lint executable eval-harness scenarios (CI gate; needs no candidate outputs).
# Distinct from `make evals` (the declarative flagship-evals prompt matrix).
eval-harness:
	python3 eval-harness/run_evals.py

# Reproducible numeric benchmark; --strict fails on a required-gold miss.
benchmark:
	python3 benchmark/reference_pipeline.py
	python3 benchmark/check_benchmark.py --strict

# Stdlib unittest suite (no third-party deps required).
test:
	python3 -m unittest discover -s tests -p "test_*.py"

# Full local gate: everything a PR should pass.
check: validate test eval-harness benchmark

audit:
	python3 scripts/validate-repo.py --audit
