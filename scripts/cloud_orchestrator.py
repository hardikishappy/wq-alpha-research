import os
import sys
import json
import time
import re
import threading
import subprocess
import requests
import numpy as np
from pathlib import Path
from requests.auth import HTTPBasicAuth
from google import genai
from google.genai import types

# ==================== CONFIG & GUARDRAILS ====================
MAX_DAILY_SIMULATIONS = 60          # Prevents burning daily WQ simulation quota
POLL_INTERVAL_SECONDS = 12          # Polling backoff interval
API_BASE = "https://api.worldquantbrain.com"

# 1. BRAIN Authentication Credentials
username = os.getenv("WQ_BRAIN_USERNAME")
password = os.getenv("WQ_BRAIN_PASSWORD")
if not (username and password):
    cred_file = Path("credential.txt")
    if cred_file.exists():
        with open(cred_file, encoding="utf-8") as f:
            username, password = json.load(f)
    else:
        raise ValueError("Missing WQ credentials in environment or credential.txt")

session = requests.Session()
session.auth = HTTPBasicAuth(username, password)
session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

# 2. Gemini API Client Initialization
gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key:
    key_file = Path("gemini_key.txt")
    if key_file.exists():
        gemini_api_key = key_file.read_text(encoding="utf-8").strip()

if not gemini_api_key:
    raise ValueError("GEMINI_API_KEY environment variable or gemini_key.txt is required.")

client = genai.Client(api_key=gemini_api_key)

# 3. Telegram Bot Configuration (reads from gitignored telegram_config.json)
_cfg_file = Path("telegram_config.json")
_tg_cfg = {}
if _cfg_file.exists():
    try:
        _tg_cfg = json.loads(_cfg_file.read_text(encoding="utf-8"))
    except Exception:
        pass

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or _tg_cfg.get("bot_token", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or _tg_cfg.get("chat_id", "")

# 4. Global State Tracking for Health Checks
orchestrator_state = {
    "start_time": time.time(),
    "simulations_today": 0,
    "last_tested": "None yet",
    "last_result": "Pending",
    "status": "Booting up...",
    "total_active": 0,
    "last_error": "None"
}

def send_telegram_alert(message: str):
    """Sends a markdown-formatted message to the designated Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=25)
        return True
    except Exception:
        return False

def telegram_command_listener():
    """Background listener that answers /status, /ping, and health checks on Telegram."""
    last_update_id = 0
    poll_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Flush old pending commands on startup
    try:
        flush_resp = requests.get(poll_url, params={"offset": -1, "timeout": 5}, timeout=10)
        if flush_resp.status_code == 200:
            results = flush_resp.json().get("result", [])
            if results:
                last_update_id = results[-1]["update_id"]
    except Exception:
        pass

    while True:
        try:
            params = {"offset": last_update_id + 1, "timeout": 20}
            resp = requests.get(poll_url, params=params, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                for update in data.get("result", []):
                    last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "").strip().lower()

                    if chat_id == str(TELEGRAM_CHAT_ID):
                        if text in ("/status", "/ping", "status", "ping", "help"):
                            uptime_hours = (time.time() - orchestrator_state["start_time"]) / 3600
                            reply = (
                                f"🟢 *WorldQuant Orchestrator: ONLINE*\n\n"
                                f"⏱ *Uptime:* `{uptime_hours:.2f} hrs`\n"
                                f"📡 *Status:* `{orchestrator_state['status']}`\n"
                                f"🎯 *Active Alphas on Account:* `{orchestrator_state['total_active']}`\n"
                                f"📊 *Simulations Today:* `{orchestrator_state['simulations_today']}/{MAX_DAILY_SIMULATIONS}`\n\n"
                                f"🔬 *Last Candidate:*\n`{orchestrator_state['last_tested']}`\n"
                                f"📈 *Last Result:* `{orchestrator_state['last_result']}`\n"
                                f"⚠️ *Last Error:* `{orchestrator_state['last_error']}`"
                            )
                            requests.post(send_url, json={"chat_id": chat_id, "text": reply, "parse_mode": "Markdown"}, timeout=25)
        except Exception:
            pass
        time.sleep(2)

# Start interactive Telegram listener in a background daemon thread
threading.Thread(target=telegram_command_listener, daemon=True).start()

# ==========================================
# 5. Universal Field Dictionary Loader
# ==========================================
field_file = Path("references/wq_usa_top3000_delay1_data_fields.json")
if not field_file.exists():
    raise FileNotFoundError(f"Missing field dictionary at {field_file}")

fields_data = json.loads(field_file.read_text(encoding="utf-8"))

valid_fields_meta = {}
for f in fields_data:
    fid = f.get("id")
    cov = f.get("coverage", 0)
    ftype = str(f.get("type", "MATRIX")).upper()
    category = str(f.get("category", "")).upper()
    
    if fid and cov > 0.45:
        is_vector = (ftype == "VECTOR") or ("VECTOR" in category)
        is_event = (ftype == "EVENT") or ("EVENT" in category) or fid.startswith("anl") or fid.startswith("nws")
        
        if is_vector:
            valid_fields_meta[fid] = "VECTOR"
        elif is_event:
            valid_fields_meta[fid] = "EVENT"
        else:
            valid_fields_meta[fid] = "MATRIX"

valid_field_names = list(valid_fields_meta.keys())
print(f"Universal Field Pool: {len(valid_field_names)} active fields across Matrix, Vector, and Event feeds.")


def wrap_field_for_fastexpr(field_id):
    """Safely converts sparse event and multidimensional vector feeds into continuous daily matrices."""
    ftype = valid_fields_meta.get(field_id, "MATRIX")
    if ftype == "VECTOR":
        return f"ts_backfill(vec_avg({field_id}), 252)"
    elif ftype == "EVENT":
        return f"ts_backfill({field_id}, 252)"
    return field_id


def authenticate_brain():
    for _ in range(5):
        try:
            resp = session.post(f"{API_BASE}/authentication", timeout=15)
            if resp.status_code in (200, 201):
                return True
        except Exception:
            pass
        time.sleep(4)
    return False


def fetch_pnl(alpha_id):
    try:
        r = session.get(f"{API_BASE}/alphas/{alpha_id}/recordsets/pnl", timeout=20)
        if r.status_code != 200 or not r.text.strip():
            return []
        data = r.json()
        props = data.get("schema", {}).get("properties", [])
        if isinstance(props, list):
            date_idx = next((i for i, p in enumerate(props) if p.get("name", "").lower() == "date"), 0)
            pnl_idx = next((i for i, p in enumerate(props) if p.get("name", "").lower() in ("pnl", "cum_pnl", "returns", "ret")), 1)
        else:
            date_idx = next((v["index"] for k, v in props.items() if k.lower() == "date"), 0)
            pnl_idx = next((v["index"] for k, v in props.items() if k.lower() in ("pnl", "cum_pnl", "returns", "ret")), 1)
        records = sorted(data.get("records", []), key=lambda row: row[date_idx])
        out = []
        for row in records:
            rec = row[0] if isinstance(row, list) and len(row) == 1 and isinstance(row[0], list) else row
            try:
                out.append(float(rec[pnl_idx]))
            except Exception:
                continue
        return out
    except Exception:
        return []


def daily_returns(cum_pnl):
    return [cum_pnl[i+1] - cum_pnl[i] for i in range(len(cum_pnl) - 1)]


def get_active_alphas():
    all_alphas = []
    offset = 0
    while True:
        try:
            resp = session.get(f"{API_BASE}/users/self/alphas", params={"limit": 100, "offset": offset}, timeout=20)
            data = resp.json()
            batch = data.get("results", data.get("alphas", []))
            if not batch:
                break
            all_alphas.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
        except Exception:
            break
    return [a for a in all_alphas if a.get("status") == "ACTIVE"]


def generate_candidate_with_gemini(active_alpha_summaries):
    """Prompts Gemini with verified fields, unit-safety rules, and proven SKILL.md templates."""
    sampled_keys = np.random.choice(valid_field_names, 30, replace=False).tolist()
    wrapped_field_pool = [wrap_field_for_fastexpr(k) for k in sampled_keys]
    
    prompt = f"""
You are an elite quantitative researcher at WorldQuant BRAIN designing cross-asset alphas for USA TOP3000 delay=1.
Goal: Produce 1 mathematically valid FastExpr expression using diverse datasets (analyst revisions, news, balance sheet, or sentiment).

### VERIFIED FIELD POOL (Ready-to-use continuous signals):
{wrapped_field_pool}

Pricing fields available: close, open, high, low, volume, vwap, returns.

### CRITICAL FASTEXPR SYNTAX RULES:
1. NEVER multiply two group_rank terms together. Structure must be strictly ADDITIVE or SUBTRACTIVE:
   0.5 * group_rank(ts_rank(SIGNAL_A, 126), subindustry) - 0.5 * group_rank(ts_rank(SIGNAL_B, 20), subindustry)
2. UNIT COMPLIANCE (unitHandling: VERIFY):
   - NEVER add a raw scalar float (like + 0.0001 or + 0.001) to a fundamental, price, or volume field (triggers Unit[] mismatch).
   - FastExpr handles zero-division natively via nanHandling: ON. Divide fields directly: (close / vwap - 1) or (field_A / field_B).
   - If subtracting or combining disparate metrics, rank them FIRST to convert to dimensionless percentiles:
     group_rank(ts_rank(A, 126), subindustry) - group_rank(ts_rank(B, 20), subindustry)
3. If combining a slow fundamental or analyst signal with a price signal, subtract price momentum to capture mean-reversion.

### DO NOT CORRELATE WITH ACTIVE ALPHAS:
{active_alpha_summaries[:4]}

Output strictly valid JSON with keys:
"expression": string,
"decay": integer (4 to 16),
"neutralization": "SUBINDUSTRY"
"""
    candidate_models = ["gemini-3.5-flash-lite", "gemini-3.8-flash"]
    
    for model_name in candidate_models:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.35,
                        tool_config=types.ToolConfig(
                            function_calling_config=types.FunctionCallingConfig(
                                mode=types.FunctionCallingConfigMode.NONE
                            )
                        )
                    ),
                )
                return json.loads(response.text)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    time.sleep(40)
                elif "503" in err_str or "UNAVAILABLE" in err_str:
                    time.sleep((2 ** attempt) * 4)
                else:
                    break
    return None


def run_loop():
    if not authenticate_brain():
        print("Failed to authenticate with WorldQuant BRAIN.")
        orchestrator_state["status"] = "Auth Failure"
        orchestrator_state["last_error"] = "BRAIN 401/Auth Failed"
        sys.exit(1)
        
    print("Orchestrator online. Loading active portfolio...")
    orchestrator_state["status"] = "Syncing portfolio"
    active_alphas = get_active_alphas()
    existing_pnls = {}
    for a in active_alphas:
        pnl = fetch_pnl(a["id"])
        if pnl:
            existing_pnls[a["id"]] = pnl
            
    orchestrator_state["total_active"] = len(active_alphas)
    orchestrator_state["status"] = "Mining alphas"
    print(f"Tracking {len(active_alphas)} ACTIVE alphas in memory.")
    
    daily_sim_count = 0
    last_reset_day = time.gmtime().tm_yday
    fast_pattern = re.compile(r"\b(close|open|vwap|volume|high|low|returns|adv\d+)\b", re.IGNORECASE)
    
    while True:
        current_day = time.gmtime().tm_yday
        if current_day != last_reset_day:
            daily_sim_count = 0
            last_reset_day = current_day
            
        if daily_sim_count >= MAX_DAILY_SIMULATIONS:
            orchestrator_state["status"] = "Daily limit reached (sleeping)"
            print(f"[Guardrail] Daily simulation limit ({MAX_DAILY_SIMULATIONS}) reached. Sleeping 1 hour...")
            time.sleep(3600)
            continue
            
        orchestrator_state["status"] = "Synthesizing candidate with Gemini"
        active_summaries = [f"ID {a['id']}: {a.get('regular', '')}" for a in active_alphas[-5:]]
        candidate = generate_candidate_with_gemini(active_summaries)
        time.sleep(10)
        
        if not candidate or "expression" not in candidate:
            continue
            
        expr = candidate["expression"]
        try:
            decay = int(candidate.get("decay", 4))
        except (ValueError, TypeError):
            decay = 4

        neutralization = candidate.get("neutralization", "SUBINDUSTRY")
        
        # Enforce turnover dampening if price/volume features are present
        if fast_pattern.search(expr) and decay < 12:
            decay = 12
            
        orchestrator_state["last_tested"] = expr[:120] + "..." if len(expr) > 120 else expr
        orchestrator_state["simulations_today"] = daily_sim_count
        orchestrator_state["status"] = f"Simulating on cluster (Decay: {decay})"
        
        print(f"\n--- Testing Candidate: {expr} (Decay: {decay}) ---")
        settings = {
            "instrumentType": "EQUITY", "region": "USA", "universe": "TOP3000",
            "delay": 1, "decay": decay, "neutralization": neutralization,
            "truncation": 0.08, "pasteurization": "ON", "unitHandling": "VERIFY",
            "nanHandling": "ON", "language": "FASTEXPR", "visualization": False
        }
        
        payload = {"type": "REGULAR", "settings": settings, "regular": expr}
        
        try:
            sim_resp = session.post(f"{API_BASE}/simulations", json=payload, timeout=25)
        except Exception as e:
            orchestrator_state["last_error"] = f"Post error: {str(e)[:60]}"
            print(f">> Simulation connection error: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
            
        daily_sim_count += 1
        orchestrator_state["simulations_today"] = daily_sim_count
        
        if sim_resp.status_code == 401:
            orchestrator_state["status"] = "Session expired, re-authenticating"
            print(">> Session expired. Re-authenticating with BRAIN...")
            authenticate_brain()
            time.sleep(4)
            continue
            
        if sim_resp.status_code != 201:
            orchestrator_state["last_error"] = f"HTTP {sim_resp.status_code}: {sim_resp.text[:60]}"
            print(f">> Simulation rejected by BRAIN ({sim_resp.status_code}): {sim_resp.text}")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
            
        loc = sim_resp.headers.get("Location", "")
        if loc:
            sim_id = loc.rstrip("/").split("/")[-1]
        else:
            try:
                sim_id = sim_resp.json().get("id")
            except Exception:
                sim_id = None

        if not sim_id:
            orchestrator_state["last_error"] = "Failed to parse simulation ID"
            print(f">> Could not extract simulation ID from BRAIN response.")
            continue
        
        # Poll completion status
        alpha_id = None
        for _ in range(35):
            time.sleep(5)
            try:
                poll_resp = session.get(f"{API_BASE}/simulations/{sim_id}", timeout=15)
                if poll_resp.status_code != 200:
                    continue
                p_data = poll_resp.json()
                status = p_data.get("status")
                
                if status == "COMPLETE":
                    alpha_id = p_data.get("alpha")
                    break
                elif status in ("ERROR", "FAILED"):
                    err_msg = p_data.get('message', 'Syntax/Execution error')
                    orchestrator_state["last_error"] = err_msg[:80]
                    print(f">> Simulation failed: {err_msg}")
                    break
            except Exception:
                pass
                
        if not alpha_id:
            orchestrator_state["last_result"] = "Simulation timed out / failed"
            print(f">> Simulation timed out or did not return an alpha ID.")
            continue
            
        try:
            alpha_data = session.get(f"{API_BASE}/alphas/{alpha_id}", timeout=15).json()
        except Exception as e:
            orchestrator_state["last_error"] = f"Alpha fetch error: {str(e)[:60]}"
            continue
            
        is_m = alpha_data.get("is", {})
        sharpe = float(is_m.get("sharpe", 0))
        fitness = float(is_m.get("fitness", 0))
        turnover = float(is_m.get("turnover", 0))
        
        result_str = f"Sharpe: {sharpe:.2f} | Fitness: {fitness:.2f} | Turnover: {turnover:.2%}"
        orchestrator_state["last_result"] = result_str
        print(f"Results -> {result_str}")
        
        # In-Sample Pass Criteria Check
        if sharpe < 1.25 or fitness < 1.0 or turnover < 0.01 or turnover > 0.20:
            print(">> Rejected: Failed In-Sample acceptance thresholds.")
            continue
            
        # Daily Return Correlation Guardrail (< 0.70 threshold)
        new_pnl = fetch_pnl(alpha_id)
        new_ret = daily_returns(new_pnl)
        high_corr = False
        
        for old_id, old_pnl in existing_pnls.items():
            old_ret = daily_returns(old_pnl)
            if len(new_ret) == len(old_ret) and len(new_ret) > 20:
                corr = abs(float(np.corrcoef(new_ret, old_ret)[0, 1]))
                if corr >= 0.70:
                    print(f">> Rejected: High correlation with existing Alpha {old_id} (corr={corr:.3f}).")
                    orchestrator_state["last_result"] += f" (Correlated {corr:.2f})"
                    high_corr = True
                    break
        if high_corr:
            continue
            
        # Auto-Submit the Alpha
        print(f">> Submitting Alpha {alpha_id} to WorldQuant BRAIN...")
        orchestrator_state["status"] = f"Submitting Alpha {alpha_id}"
        try:
            sub_resp = session.post(f"{API_BASE}/alphas/{alpha_id}/submit", timeout=20)
        except Exception as e:
            print(f">> Error submitting alpha: {e}")
            continue
            
        if sub_resp.status_code not in (200, 201):
            orchestrator_state["last_error"] = f"Submit failed: HTTP {sub_resp.status_code}"
            print(f">> Submission error (Status {sub_resp.status_code}): {sub_resp.text}")
            continue
            
        # Confirm ACTIVE Status
        is_active = False
        for _ in range(25):
            time.sleep(8)
            try:
                chk = session.get(f"{API_BASE}/alphas/{alpha_id}", timeout=15).json()
                if chk.get("status") == "ACTIVE":
                    is_active = True
                    break
            except Exception:
                pass
                
        if is_active:
            print(f"\n==========================================")
            print(f"🎉 SUCCESS! Alpha {alpha_id} is now ACTIVE!")
            print(f"==========================================\n")
            
            existing_pnls[alpha_id] = new_pnl
            active_alphas.append(chk)
            total_active = len(active_alphas)
            orchestrator_state["total_active"] = total_active
            
            # 1. Update local skill memory
            try:
                subprocess.run([sys.executable, "scripts/evolve_skill.py", "--apply"], check=False)
            except Exception as e:
                print(f"[Warning] evolve_skill.py error: {e}")
            
            # 2. Dispatch Telegram Push Alert
            alert_msg = (
                f"🚀 *New WorldQuant Alpha Accepted!*\n\n"
                f"*Alpha ID:* `{alpha_id}`\n"
                f"*Sharpe Ratio:* `{sharpe:.2f}`\n"
                f"*Fitness:* `{fitness:.2f}`\n"
                f"*Turnover:* `{turnover:.2%}`\n"
                f"*Expression:*\n`{expr}`\n\n"
                f"📊 *Total ACTIVE Alphas:* `{total_active}`"
            )
            send_telegram_alert(alert_msg)
            
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    run_loop()