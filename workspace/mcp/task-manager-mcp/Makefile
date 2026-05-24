.PHONY: venv install run contract smoke check

venv:
	python3 -m venv .venv

install: venv
	. .venv/bin/activate && pip install -r requirements.txt

run:
	. .venv/bin/activate && python server.py

contract:
	. .venv/bin/activate && python scripts/check_tm_contract.py

smoke:
	. .venv/bin/activate && python scripts/smoke_test.py

check: contract smoke
