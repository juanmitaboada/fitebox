.PHONY: obs
obs:
	-### Start OBS Studio ... ###
	env LIBGL_ALWAYS_SOFTWARE=true obs &

.PHONY: start
start:
	-### Start recording ... ###
	(. .venv/bin/activate && python buttons_controller.py start )

.PHONY: stop
stop:
	-### Stop recording ... ###
	(. .venv/bin/activate && python buttons_controller.py stop )

.PHONY: uptime
uptime:
	-### Uptime ... ###
	(. .venv/bin/activate && python buttons_controller.py uptime )

.PHONY: shutdown
shutdown:
	-### Shutdown recording ... ###
	(. .venv/bin/activate && python buttons_controller.py shutdown )

.PHONY: env
env:
	-### Installing requirements...
	test ! -d env && virtualenv -p python3 env || true
	(. ./env/bin/activate && pip install -r requirements.txt)

.PHONY: remake_env
remake_env:
	-### Remaking environment...
	test -d env && rm -rf env
	make env
