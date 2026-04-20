.PHONY: help install run test refresh lint clean deploy

help:          ## Show this help
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:       ## Create venv and install deps
	python3 -m venv venv
	./venv/bin/pip install --upgrade pip
	./venv/bin/pip install -r requirements.txt

run:           ## Start API (gunicorn, 4 workers) + static dashboard via start.sh
	./start.sh

serve:         ## Run API alone under gunicorn (4 workers)
	gunicorn app:app -w 4 --bind 0.0.0.0:8765 --timeout 120

test:          ## Run smoke tests
	pytest tests/ -q

refresh:       ## Regenerate all dashboard JSON (contacts, analytics, ops)
	./scripts/refresh_dashboards.sh

lint:          ## Byte-compile everything to catch syntax errors
	python3 -m compileall -q api app.py collectors config database.py dashboard enrichment main.py scoring scripts utils

clean:         ## Remove caches and generated data JSON
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -f export/contacts_data.json export/analytics_data.json export/ops_data.json

deploy:        ## Push to main — VPS cron pulls and refreshes on next cycle
	git push origin main
