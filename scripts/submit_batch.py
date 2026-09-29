import os
import json
import time
import requests
import numpy as np
from pathlib import Path
from requests.auth import HTTPBasicAuth

API_BASE = "https://api.worldquantbrain.com"

# 1. Load credentials (Section 7.1)
username = os.getenv("WQ_BRAIN_USERNAME")
password = os.getenv("WQ_BRAIN_PASSWORD")
if not (username and password):
    cred_file = Path("credential.txt")
    if cred_file.exists():
        with open(cred_file, encoding="utf-8") as f:
            username, password = json.load(f)
    else:
        raise ValueError("Missing credentials in environment or credential.txt")

session = requests.Session()
session.auth = HTTPBasicAuth(username, password)
session.headers.update({
    "Content-Type": "application/json",
    "Accept": "application/json",
})

# Authenticate
resp = session.post(f"{API_BASE}/authentication")
assert resp.status_code in (200, 201), f"Authentication failed: {resp.status_code} {resp.text}"
print("Authenticated successfully with WorldQuant BRAIN.")

# 2. PnL and Daily Returns helpers (Section 7.2)
def fetch_pnl(session, alpha_id):
    r = session.get(f"{API_BASE}/alphas/{alpha_id}/recordsets/pnl")
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
    records = sorted(data.get("records", []), key=lambda r: r[date_idx])
    out = []
    for row in records:
        rec = row[0] if isinstance(row, list) and len(row) == 1 and isinstance(row[0], list) else row
        try:
            out.append(float(rec[pnl_idx]))
        except Exception:
            continue
    return out

def daily_returns(cum_pnl):
    return [cum_pnl[i+1] - cum_pnl[i] for i in range(len(cum_pnl) - 1)]

def get_active_alphas(session, user_id="self", limit=100):
    all_alphas = []
    offset = 0
    while True:
        data = session.get(f"{API_BASE}/users/{user_id}/alphas", params={"limit": limit, "offset": offset}).json()
        batch = data.get("results", data.get("alphas", []))
        if not batch:
            break
        all_alphas.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return [a for a in all_alphas if a.get("status") == "ACTIVE"]

# 3. Pull active alphas and their PnL history for self-correlation check
print("Fetching existing ACTIVE alphas from your account...")
active_alphas = get_active_alphas(session)
existing_pnls = {}
for a in active_alphas:
    aid = a.get("id")
    pnl = fetch_pnl(session, aid)
    if pnl:
        existing_pnls[aid] = pnl
print(f"Found {len(active_alphas)} ACTIVE alphas in your account.")

# 4. Proven High Win-Rate Templates from Section 4.1 & 8.1
candidates = [
    {
        # Investment Anomaly: Capital expenditure intensity (verified identifier: capex)
        "name": "Disciplined Capital Expenditure Allocation",
        "expr": "group_rank(-ts_rank(capex / assets, 252), subindustry)",
        "decay": 0,
        "neutralization": "SUBINDUSTRY"
    },
    {
        # Financial Leverage Anomaly: Conservative Balance Sheet (verified: liabilities / assets)
        # Companies reducing leverage systematically outperform over 1-year horizons
        "name": "Deleveraging Balance Sheet Quality",
        "expr": "group_rank(-ts_rank(liabilities / assets, 252), subindustry)",
        "decay": 0,
        "neutralization": "SUBINDUSTRY"
    },
    {
        # Orthogonal Hybrid: Volume Trend Divergence + Capital Profitability
        # Anchors price-volume with slow balance-sheet equity to pass IS and keep TO low
        "name": "Volume-Damped Capital Productivity",
        "expr": "0.5 * group_rank(ts_rank(operating_income / assets, 126), subindustry) - 0.5 * group_rank(ts_decay_linear((volume / adv20) - 1, 10), industry)",
        "decay": 6,
        "neutralization": "SUBINDUSTRY"
    }
]
# 5. Simulation & Submission Loop (Section 7.5 & 7.7)
for c in candidates:
    print(f"\n=======================================================")
    print(f"Testing {c['name']}")
    print(f"Expression: {c['expr']}")
    
    settings = {
        "instrumentType": "EQUITY",
        "region": "USA",
        "universe": "TOP3000",
        "delay": 1,
        "decay": c["decay"],
        "neutralization": c["neutralization"],
        "truncation": 0.08,
        "pasteurization": "ON",
        "unitHandling": "VERIFY",
        "nanHandling": "ON",
        "language": "FASTEXPR",
        "visualization": False,
    }
    
    payload = {"type": "REGULAR", "settings": settings, "regular": c["expr"]}
    resp = session.post(f"{API_BASE}/simulations", json=payload)
    if resp.status_code != 201:
        print(f"Simulation submission failed: {resp.status_code} {resp.text}")
        continue
    
    sim_id = resp.headers["Location"].rstrip("/").split("/")[-1]
    print(f"Simulation queued (ID: {sim_id}). Waiting for results...")
    
    alpha_id = None
    while True:
        data = session.get(f"{API_BASE}/simulations/{sim_id}").json()
        if data.get("status") == "COMPLETE":
            alpha_id = data["alpha"]
            break
        if data.get("status") in ("ERROR", "FAILED"):
            print(f"Simulation failed: {data}")
            break
        time.sleep(6)
        
    if not alpha_id:
        continue
        
    alpha = session.get(f"{API_BASE}/alphas/{alpha_id}").json()
    is_ = alpha.get("is", {})
    sharpe = is_.get("sharpe", 0)
    fitness = is_.get("fitness", 0)
    turnover = is_.get("turnover", 0)
    returns = is_.get("returns", 0)
    
    print(f"IS Results -> Sharpe: {sharpe} | Fitness: {fitness} | Turnover: {turnover:.4f} | Returns: {returns:.2%}")
    
    # Validation filters from Section 5.1 & 7.5
    if sharpe < 1.25 or fitness < 1.0 or turnover < 0.01 or turnover > 0.20:
        print(">> Alpha failed IS threshold check (requires Sharpe >= 1.25, Fitness >= 1.0, Turnover <= 20%). Skipping.")
        continue
        
    # Self-Correlation Check on Daily Returns (Section 7.2)
    high_corr = False
    if existing_pnls:
        new_pnl = fetch_pnl(session, alpha_id)
        new_ret = daily_returns(new_pnl)
        for old_id, old_pnl in existing_pnls.items():
            old_ret = daily_returns(old_pnl)
            if len(new_ret) == len(old_ret) and len(new_ret) > 20:
                corr = abs(float(np.corrcoef(new_ret, old_ret)[0, 1]))
                if corr >= 0.7:
                    print(f">> High correlation with active alpha {old_id} (corr={corr:.3f} >= 0.7). Skipping.")
                    high_corr = True
                    break
    if high_corr:
        continue
        
    # Submit Alpha to BRAIN (Section 7.4)
    print(">> Submitting alpha to BRAIN...")
    sub = session.post(f"{API_BASE}/alphas/{alpha_id}/submit")
    if sub.status_code not in (200, 201):
        print(f"Submit request rejected: {sub.status_code} {sub.text}")
        continue
        
    # Post-submit verification loop (Section 7.7)
    print(">> Confirming ACTIVE status...")
    verified = False
    for _ in range(30):
        time.sleep(10)
        check_alpha = session.get(f"{API_BASE}/alphas/{alpha_id}").json()
        status = check_alpha.get("status")
        if status == "ACTIVE":
            print(f">> SUCCESS: Alpha {alpha_id} is now ACTIVE on your account!")
            verified = True
            break
        sc = next((chk for chk in check_alpha.get("is", {}).get("checks", []) if chk.get("name") == "SELF_CORRELATION"), {})
        if sc.get("result") == "FAIL":
            print(f">> Submission rejected: SELF_CORRELATION check failed.")
            break
            
    if not verified:
        print(f"Alpha {alpha_id} submitted with status: {status}")