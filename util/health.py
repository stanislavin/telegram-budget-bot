import asyncio
import logging
import requests
import time
from flask import Flask, request, render_template_string
from threading import Thread
from requests.exceptions import Timeout, RequestException

from util.config import SERVICE_URL, HEALTH_CHECK_PORT, HEALTH_CHECK_HOST
from util.openrouter import process_with_openrouter
from util.sheets import save_to_sheets, get_daily_stats
from util.telegram import CATEGORIES

logger = logging.getLogger(__name__)
app = None

EXPENSE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Submit Expense</title>
    <style>
        :root {
            --bg: linear-gradient(135deg, #0f172a, #111827, #0b1220);
            --panel: rgba(255, 255, 255, 0.06);
            --panel-strong: rgba(255, 255, 255, 0.12);
            --accent: #7dd3fc;
            --accent-2: #a855f7;
            --text: #e5e7eb;
            --muted: #9ca3af;
            --danger: #fca5a5;
            --success: #86efac;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: "Space Grotesk", "Segoe UI", system-ui, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }
        .shell {
            width: min(900px, 100%);
            background: var(--panel);
            border: 1px solid var(--panel-strong);
            border-radius: 20px;
            padding: 28px;
            backdrop-filter: blur(8px);
            box-shadow: 0 20px 60px rgba(0,0,0,0.35);
        }
        h1 {
            margin: 0 0 8px;
            font-size: 28px;
            letter-spacing: -0.02em;
        }
        p.sub {
            margin: 0 0 20px;
            color: var(--muted);
        }
        form {
            display: grid;
            gap: 16px;
        }
        label {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            color: var(--muted);
            font-size: 14px;
        }
        input, select, textarea {
            width: 100%;
            background: var(--panel-strong);
            border: 1px solid transparent;
            border-radius: 12px;
            padding: 12px 14px;
            color: var(--text);
            font-size: 15px;
            transition: border 0.2s ease, transform 0.1s ease;
        }
        input:focus, select:focus, textarea:focus {
            outline: none;
            border-color: var(--accent);
            transform: translateY(-1px);
        }
        textarea { resize: vertical; min-height: 100px; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px,1fr));
            gap: 12px;
        }
        .pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 12px;
            border-radius: 12px;
            background: var(--panel-strong);
            border: 1px solid transparent;
        }
        .pill strong { color: var(--text); }
        .actions {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        button {
            border: none;
            cursor: pointer;
            font-size: 15px;
            padding: 12px 16px;
            border-radius: 12px;
            color: #0b1220;
            font-weight: 600;
            transition: transform 0.1s ease, box-shadow 0.2s ease;
        }
        button.primary {
            background: linear-gradient(135deg, var(--accent), var(--accent-2));
            color: #0d1117;
            box-shadow: 0 10px 30px rgba(125, 211, 252, 0.25);
        }
        button.secondary {
            background: var(--panel-strong);
            color: var(--text);
            border: 1px solid var(--panel-strong);
        }
        button:active { transform: translateY(1px); }
        .status {
            padding: 12px 14px;
            border-radius: 12px;
            border: 1px solid var(--panel-strong);
            background: var(--panel);
            display: grid;
            gap: 6px;
        }
        .status.error { border-color: var(--danger); color: var(--danger); }
        .status.success { border-color: var(--success); color: var(--success); }
        .status .label { text-transform: uppercase; letter-spacing: 0.06em; font-size: 12px; }
        .totals { display: flex; gap: 10px; flex-wrap: wrap; }
        .totals span {
            background: var(--panel-strong);
            padding: 8px 12px;
            border-radius: 10px;
            color: var(--text);
            border: 1px solid var(--panel-strong);
        }
        .overlay {
            position: fixed;
            inset: 0;
            display: none;
            align-items: center;
            justify-content: center;
            background: rgba(0,0,0,0.5);
            backdrop-filter: blur(4px);
            z-index: 10;
        }
        .overlay.active { display: flex; }
        .spinner {
            width: 60px;
            height: 60px;
            border-radius: 50%;
            border: 6px solid rgba(255,255,255,0.2);
            border-top-color: var(--accent);
            animation: spin 0.9s linear infinite;
            box-shadow: 0 0 30px rgba(125, 211, 252, 0.35);
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="overlay" aria-label="Loading">
        <div class="spinner"></div>
        <div class="overlay-text"></div>
    </div>
    <div class="shell">
        <h1>Submit an Expense</h1>
        {% if error %}
            <div class="status error">
                <span class="label">Issue</span>
                <div>{{ error }}</div>
            </div>
        {% endif %}
        {% if saved %}
            <div class="status success">
                <span class="label">Saved</span>
                <div>{{ saved }}</div>
                {% if totals %}
                    <div class="totals">
                        {% for currency, total in totals.items() %}
                            <span>{{ "%.2f"|format(total) }} {{ currency }}</span>
                        {% endfor %}
                    </div>
                {% endif %}
            </div>
        {% endif %}
        <form method="POST">
            <input type="hidden" name="action" value="{{ action }}">
            <div>
                <label>
                    Expense text
                </label>
                <textarea name="message" placeholder="25.50 usd food groceries" required>{{ form_data.message }}</textarea>
            </div>
            <div class="grid">
                <div>
                    <label>Amount</label>
                    <input name="amount" type="number" step="0.01" placeholder="1200.00" value="{{ form_data.amount }}">
                </div>
                <div>
                    <label>Currency</label>
                    <select name="currency">
                        {% for cur in currencies %}
                            <option value="{{ cur }}" {% if form_data.currency == cur %}selected{% endif %}>{{ cur }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div>
                    <label>Category</label>
                    <select name="category">
                        {% for cat in categories %}
                            <option value="{{ cat }}" {% if form_data.category == cat %}selected{% endif %}>{{ cat }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>
            <div>
                <label>Description</label>
                <input name="description" type="text" placeholder="at supermarket" value="{{ form_data.description }}">
            </div>
            <div class="actions">
                <button class="secondary" type="submit" data-action="parse">Analyze with AI</button>
                <button class="primary" type="submit" data-action="save">Save to Google Sheets</button>
            </div>
            {% if parsed %}
                <div class="status">
                    <span class="label">Parsed by AI</span>
                    <div>{{ parsed.amount }} {{ parsed.currency }} · {{ parsed.category }} · {{ parsed.description }}</div>
                    {% if not saved %}
                        <div id="autosave-status"></div>
                    {% endif %}
                </div>
            {% endif %}
        </form>
    </div>
    <script>
        // Keep the hidden action value in sync with the clicked button
        const hiddenAction = document.querySelector('input[name="action"]');
        const overlay = document.querySelector('.overlay');
        const overlayText = document.querySelector('.overlay-text');
        const form = document.querySelector('form');
        document.querySelectorAll('button[type="submit"]').forEach(btn => {
            btn.addEventListener('click', () => {
                hiddenAction.value = btn.dataset.action || 'parse';
                const requireFields = hiddenAction.value === 'save';
                document.querySelector('input[name="amount"]').required = requireFields;
                document.querySelector('input[name="description"]').required = requireFields;
                document.querySelector('select[name="currency"]').required = requireFields;
                document.querySelector('select[name="category"]').required = requireFields;
            });
        });
        form.addEventListener('submit', () => {
            overlayText.textContent = hiddenAction.value === 'save' ? 'Saving to Sheets…' : 'Running AI analysis…';
            overlay.classList.add('active');
        });

        // Auto-save countdown after parse
        const autoSaveEnabled = {{ 'true' if parsed and not saved else 'false' }};
        let autoSaveTimer = null;
        let countDownInterval = null;
        let autoSaving = false;
        const autoSaveSeconds = 10;
        const autoSaveStatus = document.getElementById('autosave-status');

        function clearAutoSave(message) {
            if (autoSaveTimer) clearTimeout(autoSaveTimer);
            if (countDownInterval) clearInterval(countDownInterval);
            autoSaveTimer = null;
            countDownInterval = null;
            if (autoSaveStatus && message) {
                autoSaveStatus.textContent = message;
            }
        }

        function startAutoSave() {
            if (!autoSaveEnabled || !autoSaveStatus) return;
            let remaining = autoSaveSeconds;
            autoSaveStatus.textContent = `Auto-saving in ${remaining}s…`;
            countDownInterval = setInterval(() => {
                remaining -= 1;
                if (remaining <= 0) {
                    clearInterval(countDownInterval);
                }
                if (autoSaveStatus) {
                    autoSaveStatus.textContent = remaining > 0
                        ? `Auto-saving in ${remaining}s…`
                        : 'Saving to Sheets…';
                }
            }, 1000);

            autoSaveTimer = setTimeout(() => {
                if (autoSaving) return;
                autoSaving = true;
                hiddenAction.value = 'save';
                overlayText.textContent = 'Saving to Sheets…';
                overlay.classList.add('active');
                form.submit();
            }, autoSaveSeconds * 1000);
        }

        // Cancel auto-save when any field changes
        ['input', 'select', 'textarea'].forEach(sel => {
            form.querySelectorAll(sel).forEach(el => {
                el.addEventListener('input', () => clearAutoSave('Auto-save cancelled (form changed).'));
                el.addEventListener('change', () => clearAutoSave('Auto-save cancelled (form changed).'));
            });
        });

        startAutoSave();
    </script>
</body>
</html>
"""

def build_app():
    """Create a Flask app with all routes registered."""
    flask_app = Flask(__name__)

    @flask_app.route('/health')
    def health_check():
        return 'OK', 200

    @flask_app.route('/nudge')
    def nudge():
        """Endpoint to keep the service alive."""
        return 'OK', 200

    @flask_app.route('/expense', methods=['GET', 'POST'])
    def expense_form():
        """Interactive web form for submitting expenses."""
        error = None
        saved = None
        totals = None
        parsed = None
        categories = list(CATEGORIES)
        form_data = {
            "message": "",
            "amount": "",
            "currency": "RUB",
            "category": categories[0] if categories else "",
            "description": "",
        }
        action = "parse"

        try:
            if request.method == 'POST':
                action = request.form.get('action', 'parse')
                form_data["message"] = request.form.get('message', '').strip()
                form_data["currency"] = request.form.get('currency', form_data["currency"]).strip() or "RUB"
                form_data["category"] = request.form.get('category', form_data["category"]).strip() or form_data["category"]
                form_data["description"] = request.form.get('description', '').strip()
                form_data["amount"] = request.form.get('amount', '').strip()

                if action == 'parse':
                    if not form_data["message"]:
                        raise ValueError("Please provide the expense text to analyze.")
                    parsed_data, parse_error = asyncio.run(process_with_openrouter(form_data["message"]))
                    if parse_error:
                        raise RuntimeError(parse_error)
                    amount, currency, category, description = parsed_data
                    parsed_category = category
                    if parsed_category and parsed_category not in categories:
                        categories.append(parsed_category)
                    parsed = {
                        "amount": amount,
                        "currency": currency,
                        "category": parsed_category,
                        "description": description,
                    }
                    # Prefill the editable form with parsed values
                    form_data.update({
                        "amount": amount,
                        "currency": currency,
                        "category": parsed_category,
                        "description": description,
                    })
                elif action == 'save':
                    if not form_data["amount"]:
                        raise ValueError("Amount is required before saving.")
                    amount = float(form_data["amount"])
                    currency = form_data["currency"].upper()
                    category = form_data["category"] or (categories[0] if categories else "")
                    description = form_data["description"]
                    _, save_error = asyncio.run(save_to_sheets(amount, currency, category, description))
                    if save_error:
                        raise RuntimeError(save_error)
                    currency_totals, _ = asyncio.run(get_daily_stats())
                    saved = f"Saved {amount:.2f} {currency} · {category} · {description}"
                    totals = currency_totals
                    if category and category not in categories:
                        categories.append(category)
                    parsed = {
                        "amount": amount,
                        "currency": currency,
                        "category": category,
                        "description": description,
                    }
        except Exception as exc:
            logger.error("Error handling /expense form: %s", exc)
            error = str(exc)

        return render_template_string(
            EXPENSE_TEMPLATE,
            error=error,
            saved=saved,
            totals=totals,
            parsed=parsed,
            form_data=form_data,
            categories=categories,
            currencies=["RSD", "EUR", "RUB"],
            action=action,
        )

    return flask_app


def start_health_check():
    """Start the Flask server for health checks and the expense form."""
    global app
    app = build_app()
    flask_app = app

    def run_flask():
        flask_app.run(host=HEALTH_CHECK_HOST, port=HEALTH_CHECK_PORT)
    
    # Start Flask in a separate thread
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Health check server started")
    return flask_thread

def nudge_pinger():
    """Run the nudge pinger with proper error handling and timeout."""
    nudge_url = f"{SERVICE_URL}/nudge"
    logger.info(f"Starting nudge pinger for {nudge_url}...")
    
    consecutive_failures = 0
    max_consecutive_failures = 3
    
    while True:
        try:
            # Add timeout to prevent hanging
            response = requests.get(nudge_url, timeout=10)
            if response.status_code == 200:
                logger.info(f"Successfully pinged {nudge_url}")
                consecutive_failures = 0  # Reset failure counter on success
            else:
                logger.error(f"Failed to ping {nudge_url}: {response.status_code}")
                consecutive_failures += 1
        except Timeout:
            logger.error(f"Timeout while pinging {nudge_url}")
            consecutive_failures += 1
        except RequestException as e:
            logger.error(f"Request error pinging {nudge_url}: {str(e)}")
            consecutive_failures += 1
        except Exception as e:
            logger.error(f"Unexpected error pinging {nudge_url}: {str(e)}")
            consecutive_failures += 1
        
        # If we have too many consecutive failures, log a warning
        if consecutive_failures >= max_consecutive_failures:
            logger.warning(f"Multiple consecutive failures ({consecutive_failures}) pinging {nudge_url}")
        
        time.sleep(60)  # Sleep for 1 minute

def start_nudge():
    """Run nudge pinger in a separate thread with monitoring and restart capability."""
    def monitor_and_restart():
        while True:
            nudge_thread = Thread(target=nudge_pinger)
            nudge_thread.daemon = True
            nudge_thread.start()
            
            # Wait for the thread to complete (it shouldn't unless there's an error)
            nudge_thread.join()
            
            logger.warning("Nudge pinger thread died, restarting...")
            time.sleep(5)  # Wait a bit before restarting
    
    monitor_thread = Thread(target=monitor_and_restart)
    monitor_thread.daemon = True
    monitor_thread.start()
    logger.info("Nudge pinger monitor started") 
