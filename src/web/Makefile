default:
	make web

.PHONY: simulenv
simulenv:
	python3 -m venv simulenv
	. simulenv/bin/activate && pip install -r requirements.simul

.PHONY: simul
simul:
	. simulenv/bin/activate && FITEBOX_SIMULATION=1 uvicorn fitebox_web:app --host 0.0.0.0 --port 8080 --no-access-log --reload

.PHONY: web
web:
	/usr/bin/python3 -m uvicorn fitebox_web:app --host 0.0.0.0 --port 8080 --no-access-log
