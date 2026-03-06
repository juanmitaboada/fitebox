# ========================================================
# FITEBOX MASTER MAKEFILE
# ========================================================

default:
	make up

# --- SETUP ---

.PHONY: setup
setup:
	@echo "🔧 Starting system-level setup..."
	sudo bash bin/setup.sh

# --- DOCKER MANAGEMENT ---

.PHONY: build
build:
	docker compose build

.PHONY: rebuild
rebuild:
	docker compose build --no-cache
	docker compose up -d --force-recreate

.PHONY: up
up:
	-make screen_boot
	docker compose up -d
	@echo "🚀 Fitebox services are running. Use 'make logs' to monitor."

.PHONY: up_build
up_build: build
	docker compose up -d
	@echo "🚀 Fitebox services are running. Use 'make logs' to monitor."

.PHONY: down
down:
	-make screen_shutdown
	docker compose down

.PHONY: restart
restart: down up

.PHONY: kill
kill:
	-# ### Stop the containers
	$(DOCKER_COMPOSE_TEST) kill

.PHONY: clean
clean:
	docker compose down -v --rmi local

.PHONY: prune
prune:
	-# ### Stop and remove containers, networks, AND volumes
	$(DOCKER_COMPOSE_TEST) down --volumes --rmi all --remove-orphans

.PHONY: prune_all
prune_all: cleanup kill
	-# ### Prune all from this machine
	docker system prune -a -f
	docker volume prune -a -f || docker volume prune -f

.PHONY: shell
shell:
	docker exec -it fitebox-recorder bash

.PHONY: logs
logs:
	docker compose logs -f

.PHONY: update-deps
requirements:
	@echo "🔍 Making requirements.txt inside the container..."
	# 1. Execute a temporary container
	# 2. Install pip-tools if not already present
	# 3. Compile the .in and output to stdout to save on the host
	docker compose run --rm --no-deps --entrypoint /bin/bash recorder -c "pip3 install --break-system-packages --quiet pip-tools && pip-compile --resolver=backtracking /app/requirements.in && cat requirements.txt" > src/requirements.txt
	@echo "✅ File src/requirements.txt updated and synced with the Docker environment."

# --- PLYMOUTH SCREEN CONTROL ---

.PHONY: screen_%
screen_%:
	make screen
	sudo plymouth display-message --text="$*"

.PHONY: screen
screen:
	sudo plymouth display-message --text=""

.PHONY: plymouth_restart
plymouth_restart:
	-sudo plymouth quit
	sleep 1
	sudo plymouthd --mode=boot --attach-to-session --pid-file=/tmp/plymouth.pid
	sleep 1
	sudo plymouth --show-splash

# --- DIAGNOSTICS & TESTS ---

.PHONY: diagnostics
diagnostics:
	@echo "🔍 Running diagnostics..."
	@bash src/diagnostics.sh

.PHONY: diagnostics_docker
diagnostics_docker:
	docker exec -it fitebox-recorder /app/diagnostics.sh

.PHONY: audio_detection
audio_detection:
	@echo "🔍 Running audio detection..."
	@bash src/audio_detection.sh

.PHONY: audio_detection_docker
audio_detection_docker:
	@echo "🔍 Running Docker audio detection..."
	docker exec -it fitebox-recorder /app/audio_detection.sh

.PHONY: oled_detection
oled_detection:
	@echo "🔍 Running OLED detection..."
	@bash src/oled_detection.sh

.PHONY: oled_detection_docker
oled_detection_docker:
	@echo "🔍 Running Docker oled detection..."
	docker exec -it fitebox-recorder /app/oled_detection.sh

# --- TESTS ---

.PHONY: test
test:
	@echo "🧪 Running tests..."
	docker exec -it fitebox-recorder /app/test_diagnostics.sh
