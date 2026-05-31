.PHONY: catalog validate check

catalog:
	python3 scripts/build-catalog.py

validate:
	python3 scripts/validate-repo.py
	python3 scripts/build-catalog.py --check

check: validate
