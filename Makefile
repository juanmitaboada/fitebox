# ========================================================
# FITEBOX MASTER MAKEFILE
# ========================================================

# --- IMAGE CONFIG ---
GHCR_IMAGE  = ghcr.io/juanmitaboada/fitebox
DOCKER_IMAGE = docker.io/br0th3r/fitebox
VERSION     = $(shell cat src/VERSION.txt 2>/dev/null || echo "dev")

default:
	make up

# --- SETUP ---

.PHONY: setup
setup:
	@echo "🔧 Starting system-level setup..."
	sudo bash bin/setup.sh

# --- DOCKER MANAGEMENT (development) ---

.PHONY: build
build:
	docker compose build

.PHONY: rebuild
rebuild:
	docker compose build --no-cache
	docker compose up -d --force-recreate

.PHONY: up
up:
	docker compose up -d
	@echo "🚀 Fitebox services are running. Use 'make logs' to monitor."

.PHONY: up_build
up_build: build
	docker compose up -d
	@echo "🚀 Fitebox services are running. Use 'make logs' to monitor."

.PHONY: down
down:
	docker compose down

.PHONY: restart
restart: down up

.PHONY: kill
kill:
	-docker compose kill

.PHONY: clean
clean:
	docker compose down -v --rmi local

.PHONY: cleancache
cleancache:
	@for d in __pycache__ .mypy_cache .pytest_cache .cache .tox ; do \
		find . -type d -name "$$d" -exec rm -rf {} +; \
	done
	@pyclean .

.PHONY: prune
prune:
	-docker compose down --volumes --rmi all --remove-orphans

.PHONY: prune_all
prune_all: clean
	docker system prune -a -f
	docker volume prune -a -f || docker volume prune -f

.PHONY: shell
shell:
	docker exec -it fitebox-recorder bash

.PHONY: logs
logs:
	docker compose logs -f

.PHONY: requirements
requirements:
	@echo "🔍 Generating requirements.txt inside container..."
	docker compose run --rm --no-deps --entrypoint /bin/bash recorder -c \
		"pip3 install --break-system-packages --quiet pip-tools && \
		pip-compile --resolver=backtracking /app/requirements.in && \
		cat requirements.txt" > src/requirements.txt
	@echo "✅ src/requirements.txt updated."

# --- PUBLISH (manual deploy to registries) ---

.PHONY: publish
publish: _publish_check _publish_build _publish_push
	@echo ""
	@echo "✅ Published FITEBOX v$(VERSION) to:"
	@echo "   $(GHCR_IMAGE):$(VERSION)"
	@echo "   $(DOCKER_IMAGE):$(VERSION)"
	@echo "   Both tagged as :latest"

.PHONY: _publish_check
_publish_check:
	@echo "📦 Publishing FITEBOX v$(VERSION)..."
	@echo ""
	@echo "This will push to:"
	@echo "  - $(GHCR_IMAGE):$(VERSION)"
	@echo "  - $(DOCKER_IMAGE):$(VERSION)"
	@echo ""
	@read -p "Continue? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	@echo ""
	@echo "🔑 Checking registry logins..."
	@docker login ghcr.io 2>/dev/null || \
		(echo "❌ Not logged in to ghcr.io. Run: docker login ghcr.io" && exit 1)
	@docker login docker.io 2>/dev/null || \
		(echo "❌ Not logged in to Docker Hub. Run: docker login" && exit 1)

.PHONY: _publish_build
_publish_build:
	@echo "🔨 Building image..."
	docker build \
		--build-arg FITEBOX_BUILD_MODE=official \
		-f docker/recorder/Dockerfile \
		-t fitebox:$(VERSION) \
		-t fitebox:latest \
		.

.PHONY: _publish_push
_publish_push:
	@echo "🚀 Tagging and pushing..."
	docker tag fitebox:$(VERSION) $(GHCR_IMAGE):$(VERSION)
	docker tag fitebox:$(VERSION) $(GHCR_IMAGE):latest
	docker tag fitebox:$(VERSION) $(DOCKER_IMAGE):$(VERSION)
	docker tag fitebox:$(VERSION) $(DOCKER_IMAGE):latest
	docker push $(GHCR_IMAGE):$(VERSION)
	docker push $(GHCR_IMAGE):latest
	docker push $(DOCKER_IMAGE):$(VERSION)
	docker push $(DOCKER_IMAGE):latest

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
